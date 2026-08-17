import re, io

s = open('mxproxy-worker.js', encoding='utf-8').read()

# protobufjs reader method -> proto scalar type
READER2PROTO = {
    'int32':'int32','uint32':'uint32','sint32':'sint32','fixed32':'fixed32','sfixed32':'sfixed32',
    'int64':'int64','uint64':'uint64','sint64':'sint64','fixed64':'fixed64','sfixed64':'sfixed64',
    'double':'double','float':'float','bool':'bool','string':'string','bytes':'bytes',
}

def find_decodes(s):
    """Find all '.decode=function(e,t){ ... }' bodies via brace matching."""
    out = []
    for m in re.finditer(r'\.decode=function\(e,t\)\{', s):
        i = m.end()  # just after {
        depth = 1
        j = i
        while j < len(s) and depth:
            c = s[j]
            if c == '{': depth += 1
            elif c == '}': depth -= 1
            j += 1
        body = s[i:j-1]
        out.append(body)
    return out

bodies = find_decodes(s)

types = {}  # typename -> list[(num, name, ptype, repeated)]
for body in bodies:
    tm = re.search(r'new mxroot\.messages\.([A-Za-z]+)', body)
    if not tm:
        continue
    tname = tm.group(1)
    fields = {}
    # Split body into per-field segments by markers: 'case N:' or 'i>>>3==N?'
    markers = [(m.start(), int(m.group(1) or m.group(2)))
               for m in re.finditer(r'case (\d+):|i>>>3==(\d+)\?', body)]
    for idx,(pos,num) in enumerate(markers):
        end = markers[idx+1][0] if idx+1 < len(markers) else len(body)
        seg = body[pos:end]
        repeated = '.push(' in seg
        # nested message?  NS.messages.Type.decode(
        nm = re.search(r'\.push\([A-Za-z_]+\.messages\.([A-Za-z]+)\.decode\(', seg) if repeated \
             else re.search(r'r\.[A-Za-z0-9_]+=[A-Za-z_]+\.messages\.([A-Za-z]+)\.decode\(', seg)
        if nm:
            sub = nm.group(1)
            if repeated:
                namem = re.search(r'r\.([A-Za-z0-9_]+)(?:&&|\|\|)', seg) or re.search(r'r\.([A-Za-z0-9_]+)\.push', seg)
            else:
                namem = re.search(r'r\.([A-Za-z0-9_]+)=', seg)
            if namem: fields[num]=(namem.group(1), sub, repeated); continue
        # scalar
        if repeated:
            sm = re.search(r'\.push\(e\.([A-Za-z0-9]+)\(', seg)
            namem = re.search(r'r\.([A-Za-z0-9_]+)(?:&&|\|\|)', seg) or re.search(r'r\.([A-Za-z0-9_]+)\.push', seg)
        else:
            sm = re.search(r'r\.[A-Za-z0-9_]+=e\.([A-Za-z0-9]+)\(', seg)
            namem = re.search(r'r\.([A-Za-z0-9_]+)=e\.', seg)
        if sm and namem:
            pt = READER2PROTO.get(sm.group(1))
            if pt: fields[num]=(namem.group(1), pt, repeated)
    if tname not in types or len(fields) > len(types[tname]):
        types[tname] = fields

out = io.StringIO()
out.write('syntax = "proto3";\n\npackage mxroot.messages;\n\n')
for tname in sorted(types):
    fields = types[tname]
    out.write(f'message {tname} {{\n')
    for num in sorted(fields):
        name, pt, rep = fields[num]
        rule = 'repeated ' if rep else ''
        out.write(f'  {rule}{pt} {name} = {num};\n')
    out.write('}\n\n')

open('matriks.proto','w',encoding='utf-8').write(out.getvalue())
print('Types extracted:', len(types))
for t in sorted(types):
    print(f'  {t}: {len(types[t])} fields')
print('\n--- SymbolMessage preview ---')
for num in sorted(types.get('SymbolMessage',{}))[:0]:
    pass

# --- auto-stub referenced-but-undefined message types so the .proto always compiles ---
import re as _re
_s = open('matriks.proto', encoding='utf-8').read()
_SCAL = {'double','float','int32','int64','uint32','uint64','sint32','sint64',
         'fixed32','fixed64','sfixed32','sfixed64','bool','string','bytes'}
_def = set(_re.findall(r'message\s+(\w+)\s*\{', _s))
_use = set(_re.findall(r'^\s*(?:repeated\s+)?(\w+)\s+\w+\s*=\s*\d+;', _s, _re.M))
_missing = sorted(t for t in _use if t not in _SCAL and t not in _def)
if _missing:
    _s += '\n// auto-stubbed (referenced, fields not extracted):\n'
    for t in _missing:
        _s += 'message %s {}\n' % t
    open('matriks.proto', 'w', encoding='utf-8').write(_s)
    print('auto-stubbed:', _missing)
