import bpy, json, math, os, random, sys
from mathutils import Vector

# ---------- args / paths ----------
argv = sys.argv[sys.argv.index('--') + 1:]
VKEY = argv[argv.index('--variant') + 1]

WS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WS)
DELIV = os.path.join(ROOT, '交付')
QADIR = os.path.join(WS, 'qa')
os.makedirs(DELIV, exist_ok=True)
os.makedirs(QADIR, exist_ok=True)

with open(os.path.join(ROOT, '数据', 'geometry.json'), encoding='utf-8') as f:
    D = json.load(f)

VAR = D['variants'][VKEY]
VNAME = VAR['name']
CEIL = D['heights']['ceiling']
CUT = D['cut_height_for_axo_and_top']
COL = D['colors']
DET = D['details']
SINK = 0.02
random.seed(1234)

print('=== build variant %s (%s) ===' % (VKEY, VNAME))

# ---------- clean scene ----------
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)

# ---------- materials ----------
def make_mat(name, rgb):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (*rgb, 1.0)
    b.inputs['Roughness'].default_value = 0.92
    m.diffuse_color = (*rgb, 1.0)
    return m

MAT_WALL = make_mat('wall', COL['wall'])
MAT_FLOOR = make_mat('floor', COL['floor'])
MAT_FURN = make_mat('furniture', COL['furniture'])
MAT_SEALED = make_mat('sealed_door_marker', COL['sealed_door_marker'])
MAT_RED = make_mat('demolition_marker', COL['demolition_marker'])
MAT_TRIM = make_mat('trim', COL['trim'])
MAT_LEAF = make_mat('door_leaf', COL['door_leaf'])
MAT_SOFT = make_mat('soft', COL['soft'])
MAT_ROUTE = make_mat('route', COL['route'])

# ---------- helpers ----------
WALL_BBOXES = []   # (x0,x1,y0,y1,z0,z1, id)
WALL_OBS = []      # (name, obj) 墙体对象,用于去共面
W7_PIECES = []     # (ymin,ymax,zmin,zmax)
BLOCK_BBOXES = []
DETAIL_BBOXES = [] # (x0,x1,y0,y1,z0,z1, name, group)
FURN_FP = []       # (x0,x1,y0,y1, id) 家具平面投影(含被细分项的外包)
COUNTS = {}

def box(name, x0, x1, y0, y1, z0, z1, mat, record=None):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2),
         (2, 6, 7, 3), (4, 0, 3, 7)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(v, [], f)
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    if record == 'wall':
        WALL_BBOXES.append((x0, x1, y0, y1, z0, z1, name))
        WALL_OBS.append((name, ob))
    elif record == 'block':
        BLOCK_BBOXES.append((x0, x1, y0, y1, z0, z1, name))
    return ob

def dbox(name, grp, x0, x1, y0, y1, z0, z1, mat):
    ob = box(name, x0, x1, y0, y1, z0, z1, mat)
    DETAIL_BBOXES.append((x0, x1, y0, y1, z0, z1, name, grp))
    COUNTS[grp] = COUNTS.get(grp, 0) + 1
    return ob

def bevel(ob, width, segments=3):
    mod = ob.modifiers.new('bev', 'BEVEL')
    mod.width = width
    mod.segments = segments
    return ob

def jit():
    return random.uniform(-4e-4, 4e-4)

def cyl(name, grp, cx, cy, r, z0, z1, mat, n=24):
    vs, fs = [], []
    for i in range(n):
        a = 2 * math.pi * i / n
        vs.append((cx + r * math.cos(a), cy + r * math.sin(a), z0))
        vs.append((cx + r * math.cos(a), cy + r * math.sin(a), z1))
    fs.append(tuple(range(0, 2 * n, 2)))
    fs.append(tuple(range(1, 2 * n, 2))[::-1])
    for i in range(n):
        j = (i + 1) % n
        fs.append((2 * i, 2 * j, 2 * j + 1, 2 * i + 1))
    me = bpy.data.meshes.new(name)
    me.from_pydata(vs, [], fs)
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    if grp:
        DETAIL_BBOXES.append((cx - r, cx + r, cy - r, cy + r, z0, z1, name, grp))
        COUNTS[grp] = COUNTS.get(grp, 0) + 1
    return ob

# ---------- floors ----------
for fl in D['floors']:
    box(fl['id'], fl['x'][0], fl['x'][1], fl['y'][0], fl['y'][1], -0.1, 0.0, MAT_FLOOR)

# ---------- walls ----------
all_walls = D['walls']
WBYID = {w['id']: w for w in all_walls}
WAX = {w['id']: ('x' if abs(w['from'][1] - w['to'][1]) < 1e-9 else 'y')
       for w in all_walls}

def ext_at(wall, pt):
    ots = [o['t'] for o in all_walls if o is not wall
           and (o['from'] == pt or o['to'] == pt)]
    return max(ots) / 2 if ots else wall['t'] / 2

def build_wall(w, openings):
    (x0, y0), (x1, y1) = w['from'], w['to']
    t = w['t']
    axis = 'x' if abs(y1 - y0) < 1e-9 else 'y'
    p0, p1 = (x0, x1) if axis == 'x' else (y0, y1)
    s0, s1 = min(p0, p1), max(p0, p1)
    e_from, e_to = ext_at(w, [x0, y0]), ext_at(w, [x1, y1])
    e0 = e_from if p0 <= p1 else e_to
    e1 = e_to if p0 <= p1 else e_from

    spans, pieces = [], []
    cur = s0
    for op in sorted(openings, key=lambda o: o[axis][0]):
        a, b = op[axis]
        if a > cur:
            spans.append((cur, a))
        z0, z1 = op['z']
        if z0 > 0:
            pieces.append((a, b, -SINK, z0))
        if z1 < CEIL - 1e-9:
            pieces.append((a, b, z1, CEIL))
        cur = max(cur, b)
    if cur < s1:
        spans.append((cur, s1))
    for a, b in spans:
        pieces.append((a, b, -SINK, CEIL))

    cx = (x0 + x1) / 2 if axis == 'y' else None
    cy = (y0 + y1) / 2 if axis == 'x' else None
    for i, (a, b, z0, z1) in enumerate(pieces):
        aa, bb = a, b
        if abs(a - s0) < 1e-9:
            aa -= e0
        if abs(b - s1) < 1e-9:
            bb += e1
        tj = t + jit()
        zt = z1 + jit()
        if axis == 'x':
            box('%s_p%d' % (w['id'], i), aa, bb, cy - tj / 2, cy + tj / 2,
                z0, zt, MAT_WALL, record='wall')
        else:
            box('%s_p%d' % (w['id'], i), cx - tj / 2, cx + tj / 2, aa, bb,
                z0, zt, MAT_WALL, record='wall')
            if w['id'] == 'W7':
                W7_PIECES.append((aa, bb, z0, zt))

SEALED = {}   # wid -> (s0,s1) 用于踢脚线断开
for w in all_walls:
    ops = [VAR['opening']] if w['id'] == 'W7' else w.get('openings') or []
    build_wall(w, ops)
    if 'sealed_door_marker' in w:
        mk = w['sealed_door_marker']
        t = w['t']
        if abs(w['from'][1] - w['to'][1]) < 1e-9:   # wall along X, faces on +/-Y
            cy = w['from'][1]
            for sgn in (1, -1):
                box('sealed_%s' % sgn, mk['x'][0], mk['x'][1],
                    cy + sgn * t / 2, cy + sgn * (t / 2 + 0.01),
                    mk['z'][0], mk['z'][1], MAT_SEALED)
            SEALED[w['id']] = ('x', cy, mk['x'])
        else:                                       # wall along Y, faces on +/-X
            cx = w['from'][0]
            for sgn in (1, -1):
                box('sealed_%s' % sgn, cx + sgn * t / 2, cx + sgn * (t / 2 + 0.01),
                    mk['y'][0], mk['y'][1], mk['z'][0], mk['z'][1], MAT_SEALED)
            SEALED[w['id']] = ('y', cx, mk['y'])

# ---------- 去共面:删除被其他墙段体积掩埋的墙面 ----------
# 墙角交接处墙段互相延伸重叠(如 W7_p2 伸入 W6),两段墙的外皮相差仅
# ~40µm,Workbench 轮廓/谷线在这些近共面区域产生竖向网点杂纹。
# 只删被埋面,不改任何墙段包围盒(删除的面是藏在别人体内的内面,
# 外表面由仍在的墙段面原样呈现,顶点也仍被其余面使用,bbox 不变)。
def _clean_wall_faces():
    EPS = 1e-5
    for nm, ob in WALL_OBS:
        me = ob.data
        others = [b for b in WALL_BBOXES if b[6] != nm]
        new_vs, new_fs = [], []
        changed = False
        for poly in me.polygons:
            verts = [me.vertices[i].co for i in poly.vertices]
            ax = max(range(3), key=lambda i: abs(poly.normal[i]))
            oa, ob_ = [i for i in range(3) if i != ax]
            cc = verts[0][ax]
            us = [v[oa] for v in verts]
            vs_ = [v[ob_] for v in verts]
            rects = [(min(us), max(us), min(vs_), max(vs_))]
            keep_all = rects[:]
            for (x0, x1, y0, y1, wz0, wz1, _n2) in others:
                rng = ((x0, x1), (y0, y1), (wz0, wz1))
                n0, n1 = rng[ax]
                if not (n0 + EPS < cc < n1 - EPS):
                    continue
                q = (rng[oa][0], rng[oa][1], rng[ob_][0], rng[ob_][1])
                nxt = []
                for r in rects:
                    if q[1] <= r[0] + 1e-9 or q[0] >= r[1] - 1e-9 or \
                       q[3] <= r[2] + 1e-9 or q[2] >= r[3] - 1e-9:
                        nxt.append(r)
                        continue
                    if r[0] < q[0] - 1e-9:
                        nxt.append((r[0], q[0], r[2], r[3]))
                    if r[1] > q[1] + 1e-9:
                        nxt.append((q[1], r[1], r[2], r[3]))
                    a0, a1 = max(r[0], q[0]), min(r[1], q[1])
                    if r[2] < q[2] - 1e-9:
                        nxt.append((a0, a1, r[2], q[2]))
                    if r[3] > q[3] + 1e-9:
                        nxt.append((a0, a1, q[3], r[3]))
                rects = nxt
                if not rects:
                    break
            if rects != keep_all:
                changed = True
            cyclic = (oa, ob_, ax) in ((0, 1, 2), (1, 2, 0), (2, 0, 1))
            flip = (poly.normal[ax] > 0) != cyclic
            for (u0, u1, v0, v1) in rects:
                base = len(new_vs)
                for uu, vv in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)):
                    co = [0.0, 0.0, 0.0]
                    co[ax] = cc
                    co[oa] = uu
                    co[ob_] = vv
                    new_vs.append(tuple(co))
                new_fs.append((base + 3, base + 2, base + 1, base)
                              if flip else (base, base + 1, base + 2, base + 3))
        if not changed:
            continue
        me2 = bpy.data.meshes.new(nm + '_c')
        me2.from_pydata(new_vs, [], new_fs)
        me2.materials.append(MAT_WALL)
        ob.data = me2
        bpy.data.meshes.remove(me)

_clean_wall_faces()

# ---------- blocks(外包不变的固定体块;被细分的床/梳妆台/衣柜不再整体建) ----------
REPLACED = {'bed', 'bed_headboard', 'dresser', 'wardrobe_west'}
for b in D['fixed_blocks']:
    FURN_FP.append((b['x'][0], b['x'][1], b['y'][0], b['y'][1], b['id']))
    if b['id'] in REPLACED:
        continue
    box(b['id'], *b['x'], *b['y'], *b['z'], MAT_FURN, record='block')
if VAR.get('wardrobe_east'):
    wb = VAR['wardrobe_east']
    FURN_FP.append((wb['x'][0], wb['x'][1], wb['y'][0], wb['y'][1], 'wardrobe_east'))

# ---------- demolition marker ----------
oy = VAR['opening']['y']
box('demolition_marker', 2.95, 3.05, oy[0], oy[1], 0.0, 0.005, MAT_RED)

# ================= details =================
EMBED = 0.0005   # 贴面构件压入墙面/基体的深度(<=1mm,不算相交)
SKIRT_BREAK = {} # ('x'|'y', round(face_coord,2), sgn) -> [(s0,s1)]

def add_break(naxis, face_coord, sgn, s0, s1):
    key = (naxis, round(face_coord, 2), sgn)
    SKIRT_BREAK.setdefault(key, []).append((s0, s1))

# 封堵标记板贴墙面 1cm,踢脚线在其范围断开
for wid, (sax, cc, mr) in SEALED.items():
    w = WBYID[wid]
    if sax == 'y':  # 沿 Y 的墙,面在 x 方向
        for sgn in (-1, 1):
            add_break('x', cc + sgn * w['t'] / 2, sgn, mr[0] - 0.002, mr[1] + 0.002)
    else:
        for sgn in (-1, 1):
            add_break('y', cc + sgn * w['t'] / 2, sgn, mr[0] - 0.002, mr[1] + 0.002)

# ---- 1. door casings ----
def clip_span(t0, t1, wself, naxis, band0, band1, z0, z1):
    """把贴面板在切向 [t0,t1] 内裁剪,避开垂直相交的墙段(用实际包围盒)。"""
    for (x0, x1, y0, y1, wz0, wz1, wn) in WALL_BBOXES:
        if wn.startswith(wself['id'] + '_'):
            continue
        if wz0 > z1 - 1e-9 or wz1 < z0 + 1e-9:
            continue
        if naxis == 'y':          # 面在 y 向带内,切向是 x
            if y0 > band1 - 1e-9 or y1 < band0 + 1e-9:
                continue
            lo, hi = x0, x1
        else:
            if x0 > band1 - 1e-9 or x1 < band0 + 1e-9:
                continue
            lo, hi = y0, y1
        mid = (t0 + t1) / 2
        if hi <= mid:
            t0 = max(t0, hi)
        elif lo >= mid:
            t1 = min(t1, lo)
        elif hi - mid >= mid - lo:
            t1 = min(t1, lo)
        else:
            t0 = max(t0, hi)
    return t0, t1

def door_casing(w, opening, tag):
    dc = DET['door_casing']
    fw, th = dc['face_width'], dc['thickness']
    t = w['t']
    h = opening['z'][1]
    axis = 'x' if abs(w['from'][1] - w['to'][1]) < 1e-9 else 'y'
    c = w['from'][1] if axis == 'x' else w['from'][0]   # 墙中线
    a, b = opening['x'] if axis == 'x' else opening['y']
    for sgn in (-1, 1):
        f0 = c + sgn * (t / 2 - EMBED)
        f1 = c + sgn * (t / 2 + th - EMBED)
        ya, yb = min(f0, f1), max(f0, f1)
        band = (ya, yb)
        # 门套竖板止于洞口边缘(盖住贴面板外缘),横板盖住两侧板
        la0, la1 = clip_span(a - fw, a, w, 'y' if axis == 'x' else 'x',
                             band[0], band[1], 0.0, h)
        lb0, lb1 = clip_span(b, b + fw, w, 'y' if axis == 'x' else 'x',
                             band[0], band[1], 0.0, h)
        ha0, ha1 = clip_span(a - fw, b + fw, w, 'y' if axis == 'x' else 'x',
                             band[0], band[1], h, h + fw)
        if axis == 'x':
            if la1 - la0 > 1e-9:
                dbox('dc_%s_%d_L' % (tag, sgn), 'casing', la0, la1, ya, yb, 0.0, h, MAT_TRIM)
            if lb1 - lb0 > 1e-9:
                dbox('dc_%s_%d_R' % (tag, sgn), 'casing', lb0, lb1, ya, yb, 0.0, h, MAT_TRIM)
            if ha1 - ha0 > 1e-9:
                dbox('dc_%s_%d_H' % (tag, sgn), 'casing', ha0, ha1, ya, yb, h, h + fw, MAT_TRIM)
        else:
            if la1 - la0 > 1e-9:
                dbox('dc_%s_%d_L' % (tag, sgn), 'casing', ya, yb, la0, la1, 0.0, h, MAT_TRIM)
            if lb1 - lb0 > 1e-9:
                dbox('dc_%s_%d_R' % (tag, sgn), 'casing', ya, yb, lb0, lb1, 0.0, h, MAT_TRIM)
            if ha1 - ha0 > 1e-9:
                dbox('dc_%s_%d_H' % (tag, sgn), 'casing', ya, yb, ha0, ha1, h, h + fw, MAT_TRIM)
        naxis = 'y' if axis == 'x' else 'x'
        add_break(naxis, c + sgn * t / 2, sgn, a - fw - 0.005, b + fw + 0.005)
        # 贴面门套板凸出墙面 ~12mm;落在其切向范围内的垂直墙面,
        # 踢脚线在该板覆盖带内断开(逐面登记)
        for t0, t1 in ((la0, la1), (lb0, lb1)):
            if t1 - t0 < 1e-9:
                continue
            for (x0, x1, y0, y1, wz0, wz1, wn) in WALL_BBOXES:
                pw = wn.split('_')[0]
                if WAX[pw] == axis:
                    continue
                if axis == 'x':   # 垂直面 = y 向墙的 x 面;踢脚线沿 y 走
                    for fx, fsgn in ((x1, 1), (x0, -1)):
                        if t0 - 0.02 <= fx <= t1 + 0.02 and \
                                y0 < band[1] + 0.1 and y1 > band[0] - 0.1:
                            add_break('x', fx, fsgn,
                                      band[0] - 0.005, band[1] + 0.005)
                else:
                    for fy, fsgn in ((y1, 1), (y0, -1)):
                        if t0 - 0.02 <= fy <= t1 + 0.02 and \
                                x0 < band[1] + 0.1 and x1 > band[0] - 0.1:
                            add_break('y', fy, fsgn,
                                      band[0] - 0.005, band[1] + 0.005)
    # 洞口两侧贴板会盖住贴面 0.5mm,在相邻垂直墙面上也断开踢脚线
    for edge_s in (a, b):
        for fsgn in (-1, 1):
            if axis == 'x':
                add_break('x', edge_s, fsgn, c - t / 2 - 0.002, c + t / 2 + 0.002)
            else:
                add_break('y', edge_s, fsgn, c - t / 2 - 0.002, c + t / 2 + 0.002)
    # 洞口内侧贴板(墙厚方向一圈);若贴面被相邻垂直墙掩埋(如方案 b 的
    # W7 南口贴 W8 端面),同样按切向跨度裁剪,裁完为空则不放
    i0, i1 = c - t / 2 - EMBED, c + t / 2 + EMBED
    zh0, zh1 = h - th + EMBED, h + EMBED
    if axis == 'x':
        s0, s1 = clip_span(i0, i1, w, 'x', a - EMBED, a + th - EMBED, 0.0, h)
        if s1 - s0 > 1e-9:
            dbox('dc_%s_in_L' % tag, 'casing', a - EMBED, a + th - EMBED, s0, s1, 0.0, h, MAT_TRIM)
        s0, s1 = clip_span(i0, i1, w, 'x', b - th + EMBED, b + EMBED, 0.0, h)
        if s1 - s0 > 1e-9:
            dbox('dc_%s_in_R' % tag, 'casing', b - th + EMBED, b + EMBED, s0, s1, 0.0, h, MAT_TRIM)
        s0, s1 = clip_span(a - EMBED, b + EMBED, w, 'y', i0, i1, zh0, zh1)
        if s1 - s0 > 1e-9:
            dbox('dc_%s_in_H' % tag, 'casing', s0, s1, i0, i1, zh0, zh1, MAT_TRIM)
    else:
        s0, s1 = clip_span(i0, i1, w, 'y', a - EMBED, a + th - EMBED, 0.0, h)
        if s1 - s0 > 1e-9:
            dbox('dc_%s_in_L' % tag, 'casing', s0, s1, a - EMBED, a + th - EMBED, 0.0, h, MAT_TRIM)
        s0, s1 = clip_span(i0, i1, w, 'y', b - th + EMBED, b + EMBED, 0.0, h)
        if s1 - s0 > 1e-9:
            dbox('dc_%s_in_R' % tag, 'casing', s0, s1, b - th + EMBED, b + EMBED, 0.0, h, MAT_TRIM)
        s0, s1 = clip_span(a - EMBED, b + EMBED, w, 'x', i0, i1, zh0, zh1)
        if s1 - s0 > 1e-9:
            dbox('dc_%s_in_H' % tag, 'casing', i0, i1, s0, s1, zh0, zh1, MAT_TRIM)

for w in all_walls:
    if w['id'] == 'W7':
        if VKEY in ('a', 'b'):
            door_casing(w, VAR['opening'], 'W7')
        continue
    for i, op in enumerate(w.get('openings') or []):
        if op['type'] == 'door':
            door_casing(w, op, '%s_%d' % (w['id'], i))

# ---- 2. door leaves ----
for lf in DET['door_leaves']:
    dbox(lf['id'], 'leaf', *lf['x'], *lf['y'], *lf['z'], MAT_LEAF)

# ---- 3. window frames ----
wf = DET['window_frames']
sec = wf['section']
for i, win in enumerate(wf['windows']):
    w = WBYID[win['wall']]
    cy = w['from'][1]
    x0, x1 = win['x']
    z0, z1 = win['z']
    y0, y1 = cy - sec / 2, cy + sec / 2
    dbox('wf_%d_bot' % i, 'wframe', x0, x1, y0, y1, z0, z0 + sec, MAT_TRIM)
    dbox('wf_%d_top' % i, 'wframe', x0, x1, y0, y1, z1 - sec, z1, MAT_TRIM)
    dbox('wf_%d_L' % i, 'wframe', x0, x0 + sec, y0, y1, z0 + sec, z1 - sec, MAT_TRIM)
    dbox('wf_%d_R' % i, 'wframe', x1 - sec, x1, y0, y1, z0 + sec, z1 - sec, MAT_TRIM)
    nm = win['mullions']
    for k in range(1, nm + 1):
        mx = x0 + (x1 - x0) * k / (nm + 1)
        dbox('wf_%d_m%d' % (i, k), 'wframe', mx - sec / 2, mx + sec / 2,
             y0, y1, z0 + sec, z1 - sec, MAT_TRIM)

# ---- 4. wardrobe detail ----
def wardrobe(tag, bb, front_sgn, ndoor):
    wd = DET['wardrobe_detail']
    kh, kr = wd['toe_kick_height'], wd['toe_kick_recess']
    dth, gap = wd['door_thickness'], wd['door_gap']
    hd = wd['handle']
    x0, x1 = bb['x']
    y0, y1 = bb['y']
    z0, z1 = bb['z']
    fx = x1 if front_sgn > 0 else x0          # 正面所在的 x
    bx = x0 if front_sgn > 0 else x1          # 背面
    kf = fx - front_sgn * kr                  # 踢脚前沿(内退)
    dbox('wk_%s_kick' % tag, 'wardrobe',
         min(bx, kf), max(bx, kf), y0, y1, z0, z0 + kh, MAT_FURN)
    car_f = fx - front_sgn * dth              # 柜体到门扇内表面
    dbox('wk_%s_car' % tag, 'wardrobe',
         min(bx, car_f), max(bx, car_f), y0, y1, z0 + kh, z1, MAT_FURN)
    L = y1 - y0
    dw = (L - (ndoor - 1) * gap) / ndoor
    dz0, dz1 = z0 + kh, z1
    edges = [y0 + i * (dw + gap) for i in range(ndoor)]
    seam_ct = {}                              # 每对门中缝坐标 -> 两扇门索引
    for i in range(ndoor):
        da, db = edges[i], edges[i] + dw
        dbox('wk_%s_d%d' % (tag, i), 'wardrobe',
             min(car_f, fx), max(car_f, fx), da, db, dz0, dz1, MAT_FURN)
    hz0, hz1 = hd['center_z'] - hd['length'] / 2, hd['center_z'] + hd['length'] / 2
    hs = hd['section']
    hx0 = fx + front_sgn * hd['standoff']
    hx1 = hx0 + front_sgn * hs
    nhandle = 0
    for pair in range(ndoor // 2):
        seam = edges[2 * pair + 1] - gap / 2  # 两扇门之间的缝中心
        for di, side in ((2 * pair, -1), (2 * pair + 1, 1)):
            hc = seam + side * hd['offset_from_door_edge']
            dbox('wk_%s_h%d' % (tag, nhandle), 'wardrobe',
                 min(hx0, hx1), max(hx0, hx1), hc - hs / 2, hc + hs / 2,
                 hz0, hz1, MAT_FURN)
            for pz in (hz0 + 0.03, hz1 - 0.03):
                dbox('wk_%s_hp%d_%d' % (tag, nhandle, 0 if pz < hd['center_z'] else 1),
                     'wardrobe', min(fx - front_sgn * EMBED, hx0 + front_sgn * EMBED),
                     max(fx - front_sgn * EMBED, hx0 + front_sgn * EMBED),
                     hc - 0.008, hc + 0.008, pz - 0.006, pz + 0.006, MAT_FURN)
            nhandle += 1
    COUNTS['wardrobe_doors_' + tag] = ndoor
    COUNTS['wardrobe_handles_' + tag] = nhandle

ww = [b for b in D['fixed_blocks'] if b['id'] == 'wardrobe_west'][0]
wardrobe('west', ww, +1, DET['wardrobe_detail']['door_counts']['wardrobe_west'])
if VAR.get('wardrobe_east'):
    wardrobe('east', VAR['wardrobe_east'], -1,
             DET['wardrobe_detail']['door_counts']['wardrobe_east'][VKEY])

# ---- 5. bed detail ----
bd = DET['bed_detail']
fr = bd['frame']
dbox('bed_frame', 'bed', *fr['x'], *fr['y'], *fr['z'], MAT_FURN)
mt = bd['mattress']
bevel(dbox('bed_mattress', 'bed', *mt['x'], *mt['y'], *mt['z'], MAT_SOFT), mt['bevel'])
for i, pl in enumerate(bd['pillows']):
    bevel(dbox('bed_pillow_%d' % i, 'bed', *pl['x'], *pl['y'], *pl['z'], MAT_SOFT), pl['bevel'])
hb = bd['headboard']
dbox('bed_headboard', 'bed', *hb['x'], *hb['y'], *hb['z'], MAT_FURN)
for i, ns in enumerate(bd['nightstands']):
    dbox('nightstand_%d' % i, 'bed', *ns['x'], *ns['y'], *ns['z'], MAT_FURN)
    FURN_FP.append((ns['x'][0], ns['x'][1], ns['y'][0], ns['y'][1], 'nightstand_%d' % i))
FURN_FP.append((4.4, 6.4, -4.1, -2.3, 'bed_all'))

# ---- 6. dresser detail ----
dd = DET['dresser_detail']
tp = dd['top']
dbox('dresser_top', 'dresser', *tp['x'], *tp['y'], *tp['z'], MAT_FURN)
dgap = 0.003
pin = 0.004
for pi, ped in enumerate(dd['pedestals']):
    x0, x1 = ped['x']
    y0, y1 = ped['y']
    z0, z1 = ped['z']
    dbox('dresser_ped%d' % pi, 'dresser', x0, x1, y0, y1, z0, z1, MAT_FURN)
    ph = (z1 - z0 - 2 * dgap) / 3
    for k in range(3):
        pz0 = z0 + k * (ph + dgap)
        pz1 = pz0 + ph
        dbox('dresser_dr%d_%d' % (pi, k), 'dresser',
             x0 + pin, x1 - pin, y1 - EMBED, y1 + 0.015 - EMBED, pz0, pz1, MAT_FURN)
        hc = (x0 + x1) / 2
        hz = pz1 - 0.035
        dbox('dresser_h%d_%d' % (pi, k), 'dresser',
             hc - 0.03, hc + 0.03, y1 + 0.015 - EMBED + 0.008, y1 + 0.015 - EMBED + 0.02,
             hz - 0.006, hz + 0.006, MAT_FURN)
        for sx in (-0.024, 0.024):
            dbox('dresser_hp%d_%d_%d' % (pi, k, 0 if sx < 0 else 1), 'dresser',
                 hc + sx - 0.004, hc + sx + 0.004,
                 y1 + 0.015 - 2 * EMBED, y1 + 0.015 - EMBED + 0.008,
                 hz - 0.003, hz + 0.003, MAT_FURN)
st = dd['stool']
cyl('dresser_stool', 'dresser', st['center'][0], st['center'][1], st['radius'],
    st['z'][0], st['z'][1], MAT_FURN)
FURN_FP.append((st['center'][0] - st['radius'], st['center'][0] + st['radius'],
                st['center'][1] - st['radius'], st['center'][1] + st['radius'], 'stool'))

# ---- 7. bay cushion ----
bc = DET['bay_cushion']
bevel(dbox('bay_cushion', 'cushion', *bc['x'], *bc['y'], *bc['z'], MAT_SOFT), bc['bevel'])

# ---- 8. skirting ----
sk = DET['skirting']
SKH, SKT = sk['height'], sk['thickness']
SK_BLOCK = []   # (x0,x1,y0,y1) 踢脚线遮挡物(衣柜/床头板/床头柜/梳妆台/飘窗台)
for fid in ('wardrobe_west', 'bed_headboard', 'dresser', 'bay_platform'):
    b = [x for x in D['fixed_blocks'] if x['id'] == fid][0]
    SK_BLOCK.append((b['x'][0], b['x'][1], b['y'][0], b['y'][1]))
if VAR.get('wardrobe_east'):
    wb = VAR['wardrobe_east']
    SK_BLOCK.append((wb['x'][0], wb['x'][1], wb['y'][0], wb['y'][1]))
for ns in bd['nightstands']:
    SK_BLOCK.append((ns['x'][0], ns['x'][1], ns['y'][0], ns['y'][1]))

FLOORS = {f['id']: f for f in D['floors']}

def wall_at(px, py, pz):
    for (x0, x1, y0, y1, z0, z1, _n) in WALL_BBOXES:
        if x0 - 1e-9 <= px <= x1 + 1e-9 and y0 - 1e-9 <= py <= y1 + 1e-9 \
                and z0 - 1e-9 <= pz <= z1 + 1e-9:
            return True
    return False

sk_segments = 0
for rid in sk['rooms']:
    r = FLOORS[rid]
    rx0, rx1 = r['x']
    ry0, ry1 = r['y']
    # (edge_name, naxis, edge_coord, normal_sign, s0, s1)
    edges = [('S', 'y', ry0, +1, rx0, rx1), ('N', 'y', ry1, -1, rx0, rx1),
             ('W', 'x', rx0, +1, ry0, ry1), ('E', 'x', rx1, -1, ry0, ry1)]
    for en, naxis, ec, nsgn, s0, s1 in edges:
        wax_need = 'x' if naxis == 'y' else 'y'   # 只取与边平行的墙的侧面
        mask = []   # 每个采样点: None 或面坐标 fc
        s = s0
        while s < s1 - 1e-9:
            smid = s + 0.0025
            hit_fc = None
            for (x0, x1, y0, y1, z0, z1, _n) in WALL_BBOXES:
                if z0 > 0.1:
                    continue
                if WAX[_n.split('_')[0]] != wax_need:
                    continue
                fc = (y1 if naxis == 'y' else x1) if nsgn > 0 else \
                     (y0 if naxis == 'y' else x0)
                if abs(fc - ec) > 0.15:
                    continue
                lo, hi = (x0, x1) if naxis == 'y' else (y0, y1)
                if not (lo - 1e-9 <= smid <= hi + 1e-9):
                    continue
                # 房间内探点:面内侧 8mm
                pp = fc + nsgn * 0.008
                px, py = (smid, pp) if naxis == 'y' else (pp, smid)
                if not (rx0 - 1e-9 <= px <= rx1 + 1e-9 and
                        ry0 - 1e-9 <= py <= ry1 + 1e-9):
                    continue
                if wall_at(px, py, 0.04):
                    continue
                # 遮挡物:距面 <=0.095m(沿 -n 方向)且切向覆盖 smid
                blocked = False
                for (bx0, bx1, by0, by1) in SK_BLOCK:
                    blo, bhi = (bx0, bx1) if naxis == 'y' else (by0, by1)
                    if not (blo - 1e-9 <= smid <= bhi + 1e-9):
                        continue
                    bnear = (by0 if naxis == 'y' else bx0) if nsgn > 0 else \
                            (by1 if naxis == 'y' else bx1)
                    dist = (bnear - fc) if nsgn > 0 else (fc - bnear)
                    if -0.02 <= dist <= 0.095:
                        blocked = True
                        break
                if blocked:
                    continue
                # 门套/封堵板贴面断开
                brk = False
                for (bax, bfc, bsgn), rngs in SKIRT_BREAK.items():
                    if bax != naxis or bsgn != nsgn or abs(bfc - fc) > 0.03:
                        continue
                    for (bs0, bs1) in rngs:
                        if bs0 - 1e-9 <= smid <= bs1 + 1e-9:
                            brk = True
                            break
                    if brk:
                        break
                if brk:
                    continue
                hit_fc = fc
                break
            mask.append(hit_fc)
            s += 0.005
        # mask -> 同面坐标的连续区间 -> 踢脚线盒
        i = 0
        n = len(mask)
        while i < n:
            if mask[i] is None:
                i += 1
                continue
            j = i
            while j + 1 < n and mask[j + 1] is not None and \
                    abs(mask[j + 1] - mask[i]) < 0.005:
                j += 1
            seg0, seg1 = s0 + i * 0.005, s0 + j * 0.005 + 0.005
            fc = mask[i]
            i = j + 1
            if seg1 - seg0 < 0.02:
                continue
            f0 = fc - nsgn * EMBED
            f1 = fc + nsgn * (SKT - EMBED)
            lo, hi = min(f0, f1), max(f0, f1)
            if naxis == 'y':
                dbox('sk_%s_%s_%d' % (rid, en, sk_segments), 'skirting',
                     seg0, seg1, lo, hi, 0.0, SKH, MAT_TRIM)
            else:
                dbox('sk_%s_%s_%d' % (rid, en, sk_segments), 'skirting',
                     lo, hi, seg0, seg1, 0.0, SKH, MAT_TRIM)
            sk_segments += 1
print('[QA2] skirting segments=%d' % sk_segments)

# ---- 9. route(仅俯视/轴测显示) ----
rt = DET['route']
pts = [tuple(p) for p in rt['common']] + [tuple(rt['cross'][VKEY]), tuple(rt['end'])]
hw = rt['width'] / 2
rz0, rz1 = rt['z']
route_obs = []

def ribbon(pts, hw, z0, z1, mat, name):
    n = len(pts)
    L, R = [], []
    for i, p in enumerate(pts):
        if i == 0:
            d = Vector((pts[1][0] - p[0], pts[1][1] - p[1]))
        elif i == n - 1:
            d = Vector((p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]))
        else:
            d = Vector((pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]))
        d.normalize()
        m = Vector((-d.y, d.x))
        L.append(Vector((p[0], p[1])) + m * hw)
        R.append(Vector((p[0], p[1])) - m * hw)
    vs = [(p.x, p.y, z1) for p in L] + [(p.x, p.y, z1) for p in R] \
       + [(p.x, p.y, z0) for p in L] + [(p.x, p.y, z0) for p in R]
    fs = []
    for i in range(n - 1):
        fs.append((i, n + i, n + i + 1, i + 1))                    # 顶面条带
        fs.append((2 * n + i, i, i + 1, 2 * n + i + 1))            # 左侧
        fs.append((n + i, 3 * n + i, 3 * n + i + 1, n + i + 1))    # 右侧
    me = bpy.data.meshes.new(name)
    me.from_pydata(vs, [], fs)
    me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    return ob

route_obs.append(ribbon(pts, hw, rz0, rz1, MAT_ROUTE, 'route_line'))
# 末端箭头:尖端在 end,底边中点沿末段反方向 0.25
p_end = Vector(pts[-1])
d = Vector((pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]))
d.normalize()
m = Vector((-d.y, d.x))
base_c = p_end - d * 0.25
tri = [p_end, base_c + m * 0.10, base_c - m * 0.10]
vs = [(p.x, p.y, rz1) for p in tri] + [(p.x, p.y, rz0) for p in tri]
fs = [(0, 1, 2), (3, 5, 4), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)]
me = bpy.data.meshes.new('route_arrow')
me.from_pydata(vs, [], fs)
me.materials.append(MAT_ROUTE)
route_obs.append(bpy.data.objects.new('route_arrow', me))
bpy.context.collection.objects.link(route_obs[-1])
COUNTS['route'] = len(route_obs)
for ob in route_obs:
    ob.hide_render = True   # .blend 里默认对人眼视角渲染隐藏;俯视/轴测前再打开

# ================= QA =================
# ---------- QA1a: opening readback ----------
exp = VAR['opening']
sample_ys = []
y = -4.8 + 0.0005
while y < -1.3:
    sample_ys.append(y)
    y += 0.001
open_ys, clear_tops = [], []
for yy in sample_ys:
    cov = sorted([(z0, z1) for (a, b, z0, z1) in W7_PIECES if a < yy < b])
    hit1 = any(z0 <= 1.0 <= z1 for z0, z1 in cov)
    if not hit1:
        open_ys.append(yy)
        below = [z0 for z0, z1 in cov if z0 > 0.05]
        clear_tops.append(min(below) if below else CEIL)
act_y = (min(open_ys), max(open_ys))
act_ztop = min(clear_tops)
print('[QA1a] expected opening y=[%.3f, %.3f] z=[%.3f, %.3f]'
      % (exp['y'][0], exp['y'][1], exp['z'][0], exp['z'][1]))
print('[QA1a] actual   opening y=[%.3f, %.3f] z=[%.3f, %.3f]'
      % (act_y[0], act_y[1], 0.0, act_ztop))
dy = max(abs(act_y[0] - exp['y'][0]), abs(act_y[1] - exp['y'][1]))
dz = abs(act_ztop - exp['z'][1])
print('[QA1a] max dev: dy=%.4f m, dz_top=%.4f m -> %s'
      % (dy, dz, 'PASS' if dy <= 0.002 and dz <= 0.002 else 'FAIL'))

# ---------- QA1b: 墙段包围盒回归(与基线文件逐段比对) ----------
cur_b = sorted([tuple(round(v, 5) for v in b[:6]) + (b[6],) for b in WALL_BBOXES])
bpath = os.path.join(QADIR, 'wall_baseline_%s.json' % VKEY)
if os.path.exists(bpath):
    base = [tuple(r[:6]) + (r[6],) for r in json.load(open(bpath))]
    diffs = [c for c in cur_b if c not in base] + [c for c in base if c not in cur_b]
    print('[QA1b] wall bbox vs baseline: %d segments, diffs=%d -> %s'
          % (len(cur_b), len(diffs), 'PASS' if not diffs else 'FAIL'))
    for d2 in diffs[:10]:
        print('[QA1b]   diff %s' % (d2,))
else:
    json.dump(cur_b, open(bpath, 'w'))
    print('[QA1b] baseline written (%d segments)' % len(cur_b))

# ---------- QA2: 构件清点 ----------
op_cased = sum(1 for w in all_walls for opx in
               ([VAR['opening']] if w['id'] == 'W7' else w.get('openings') or [])
               if (w['id'] != 'W7' and opx.get('type') == 'door') or
                  (w['id'] == 'W7' and VKEY in ('a', 'b')))
n_leaves = sum(1 for _ in DET['door_leaves'])
n_mull = sum(w['mullions'] for w in wf['windows'])
n_fbars = sum(4 + w['mullions'] for w in wf['windows'])
dc = DET['wardrobe_detail']['door_counts']
exp_doors = {'west': dc['wardrobe_west'],
             'east': dc['wardrobe_east'][VKEY] if VAR.get('wardrobe_east') else 0}
print('[QA2] door casings: %d openings x9 boards = %d objs'
      % (op_cased, COUNTS.get('casing', 0)))
print('[QA2] door leaves=%d  window frame bars=%d (mullions=%d)  skirting=%d'
      % (n_leaves, n_fbars, n_mull, COUNTS.get('skirting', 0)))
print('[QA2] bed parts=%d (frame1+mattress1+pillows2+headboard1+nightstands2)'
      % COUNTS.get('bed', 0))
print('[QA2] dresser parts=%d  cushion=%d  route objs=%d'
      % (COUNTS.get('dresser', 0), COUNTS.get('cushion', 0), COUNTS.get('route', 0)))
for tg in ('west', 'east'):
    got = COUNTS.get('wardrobe_doors_' + tg, 0)
    print('[QA2] wardrobe_%s doors=%d expected=%d handles=%d -> %s'
          % (tg, got, exp_doors[tg], COUNTS.get('wardrobe_handles_' + tg, 0),
             'PASS' if got == exp_doors[tg] else 'FAIL'))

# ---------- QA3: 新增构件 vs 墙体/家具/其他构件组 相交检测(>1mm 才算) ----------
def ov(a, b):
    ox = min(a[1], b[1]) - max(a[0], b[0])
    oy2 = min(a[3], b[3]) - max(a[2], b[2])
    oz = min(a[5], b[5]) - max(a[4], b[4])
    return (ox, oy2, oz) if (ox > 0.001 and oy2 > 0.001 and oz > 0.001) else None

pairs = 0
hits = []
for dx in DETAIL_BBOXES:
    for wx in WALL_BBOXES:
        pairs += 1
        r = ov(dx, wx)
        if r:
            hits.append((dx[6], wx[6]) + r)
    for bx in BLOCK_BBOXES:
        pairs += 1
        r = ov(dx, bx)
        if r:
            hits.append((dx[6], bx[6]) + r)
for i in range(len(DETAIL_BBOXES)):
    for j in range(i + 1, len(DETAIL_BBOXES)):
        a, b = DETAIL_BBOXES[i], DETAIL_BBOXES[j]
        if a[7] == b[7]:
            continue
        pairs += 1
        r = ov(a, b)
        if r:
            hits.append((a[6], b[6]) + r)
print('[QA3] checked %d detail-vs-wall/block/detail pairs; intersecting(>1mm): %d'
      % (pairs, len(hits)))
for h in hits:
    print('[QA3]   %s vs %s  overlap dx=%.4f dy=%.4f dz=%.4f' % h)
if os.environ.get('DBGSK'):
    for dx in DETAIL_BBOXES:
        if dx[7] in ('skirting', 'casing'):
            print('[DBG] %-28s x[%.4f,%.4f] y[%.4f,%.4f] z[%.4f,%.4f]'
                  % (dx[6], dx[0], dx[1], dx[2], dx[3], dx[4], dx[5]))

# ---------- QA4: 动线不穿家具/墙(门洞除外),每 5cm 取样 ----------
OPEN_FP = []   # 门洞平面投影(s 向洞口范围 + 墙厚条带)
for w in all_walls:
    ops = [VAR['opening']] if w['id'] == 'W7' else w.get('openings') or []
    axis = 'x' if abs(w['from'][1] - w['to'][1]) < 1e-9 else 'y'
    c = w['from'][1] if axis == 'x' else w['from'][0]
    for opx in ops:
        if opx.get('type') != 'door' and w['id'] != 'W7':
            continue
        a, b = opx['x'] if axis == 'x' else opx['y']
        if axis == 'x':
            OPEN_FP.append((a - 0.001, b + 0.001, c - w['t'], c + w['t']))
        else:
            OPEN_FP.append((c - w['t'], c + w['t'], a - 0.001, b + 0.001))

def inside2d(px, py, bb):
    return bb[0] <= px <= bb[1] and bb[2] <= py <= bb[3]

nsamp = 0
bad = []
for i in range(len(pts) - 1):
    p0 = Vector(pts[i]); p1 = Vector(pts[i + 1])
    seglen = (p1 - p0).length
    k = max(1, int(seglen / 0.05))
    for j in range(k + 1):
        p = p0 + (p1 - p0) * (j / k)
        nsamp += 1
        in_open = any(inside2d(p.x, p.y, o) for o in OPEN_FP)
        for wx in WALL_BBOXES:
            if wx[4] > 0.1:
                continue
            if inside2d(p.x, p.y, wx) and not in_open:
                bad.append((p.x, p.y, wx[6]))
                break
        else:
            for fx2 in FURN_FP:
                if inside2d(p.x, p.y, fx2):
                    bad.append((p.x, p.y, fx2[4]))
                    break
print('[QA4] route samples=%d; inside wall/furniture footprints: %d'
      % (nsamp, len(bad)))
for bq in bad:
    print('[QA4]   point (%.3f, %.3f) inside %s' % bq)

# ---------- cameras ----------
sc = bpy.context.scene
CAMS = {}

def add_cam(key, loc, look_at, persp_lens=None, ortho_scale=None):
    cd = bpy.data.cameras.new(key)
    co = bpy.data.objects.new(key, cd)
    bpy.context.collection.objects.link(co)
    co.location = loc
    if look_at is not None:
        d = Vector(look_at) - Vector(loc)
        co.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    if persp_lens:
        cd.type = 'PERSP'
        cd.sensor_width = D['cameras'][key]['sensor_width_mm']
        cd.lens = persp_lens
        cd.clip_start = 0.05
        cd.clip_end = 80
    else:
        cd.type = 'ORTHO'
        cd.ortho_scale = ortho_scale or 10
        cd.clip_start = 0.1
        cd.clip_end = 100
    CAMS[key] = co
    return co

# scene bbox for ortho framing
xs, ys, zs = [], [], []
for fl in D['floors']:
    xs += fl['x']; ys += fl['y']
for wl in all_walls:
    xs += [wl['from'][0], wl['to'][0]]
    ys += [wl['from'][1], wl['to'][1]]
    t2 = wl['t'] / 2
    xs += [wl['from'][0] - t2, wl['from'][0] + t2, wl['to'][0] - t2, wl['to'][0] + t2]
    ys += [wl['from'][1] - t2, wl['from'][1] + t2, wl['to'][1] - t2, wl['to'][1] + t2]
zs = [-0.1, CEIL]
corners = [Vector((x, y, z)) for x in (min(xs), max(xs))
           for y in (min(ys), max(ys)) for z in zs]

def fit_ortho(cam_obj):
    R = cam_obj.rotation_euler.to_matrix()
    right, up = Vector(R.col[0]), Vector(R.col[1])
    p = cam_obj.location
    us = [(c - p).dot(right) for c in corners]
    vs = [(c - p).dot(up) for c in corners]
    du, dv = max(us) - min(us), max(vs) - min(vs)
    return max(du, dv * 4.0 / 3.0) * 1.06

axo = D['cameras']['axo']
az = math.radians(axo['azimuth_deg_west_of_south'])
el = math.radians(axo['elevation_deg'])
offdir = Vector((-math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), math.sin(el)))
tgt = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.8))
loc = tgt + offdir * 30
ca = add_cam('axo', loc, tgt)
ca.data.ortho_scale = fit_ortho(ca)

for key in ('corridor', 'dressing_door', 'bedroom'):
    c = D['cameras'][key]
    add_cam(key, c['location'], c['look_at'], persp_lens=c['lens_mm'])

ct = D['cameras']['top']
cto = add_cam('top', ct['location'], ct['look_at'])
cto.rotation_euler = (0, 0, 0)
cto.data.ortho_scale = fit_ortho(cto)
print('[CAM] axo ortho_scale=%.3f top ortho_scale=%.3f'
      % (ca.data.ortho_scale, cto.data.ortho_scale))

# ---------- render settings: Workbench uniform shading ----------
for eng in ('BLENDER_WORKBENCH_NEXT', 'BLENDER_WORKBENCH'):
    try:
        sc.render.engine = eng
        break
    except TypeError:
        continue
sh = sc.display.shading
sh.light = 'STUDIO'
sh.color_type = 'MATERIAL'
sh.show_shadows = False
sh.show_cavity = True
sh.cavity_type = 'WORLD'
for attr, val in (('cavity_ridge_factor', 1.4), ('curvature_ridge_factor', 1.4),
                  ('cavity_valley_factor', 1.8), ('curvature_valley_factor', 1.8)):
    if hasattr(sh, attr):
        setattr(sh, attr, val)
sh.show_object_outline = True
sh.object_outline_color = (0.10, 0.10, 0.12)
sh.background_type = 'WORLD'
w = bpy.data.worlds.new('W')
w.use_nodes = True
w.color = tuple(COL['background'])
bg = w.node_tree.nodes['Background']
bg.inputs['Color'].default_value = (*COL['background'], 1.0)
bg.inputs['Strength'].default_value = 1.0
sc.world = w
if hasattr(sh, 'show_specular_highlight'):
    sh.show_specular_highlight = False
for attr, val in (('render_aa', '8'),):
    if hasattr(sc.display, attr):
        try:
            setattr(sc.display, attr, val)
        except TypeError:
            pass
sc.render.resolution_x = 1600
sc.render.resolution_y = 1200
sc.render.image_settings.file_format = 'PNG'
sc.view_settings.view_transform = 'Standard'
print('[RENDER] engine=%s' % sc.render.engine)

# ---------- save .blend (full-height model), render eye views ----------
if os.environ.get('NORENDER'):
    print('=== done %s (no render) ===' % VKEY)
    sys.exit(0)
blend_path = os.path.join(DELIV, '%s_%s.blend' % (VKEY, VNAME))
bpy.ops.wm.save_as_mainfile(filepath=blend_path)

def render(key, fn):
    sc.camera = CAMS[key]
    sc.render.filepath = os.path.join(DELIV, fn)
    bpy.ops.render.render(write_still=True)
    print('[RENDER] %s' % sc.render.filepath)

render('corridor', '%s_%s_走道口视角.png' % (VKEY, VNAME))
render('dressing_door', '%s_%s_衣帽间门口视角.png' % (VKEY, VNAME))
render('bedroom', '%s_%s_卧室内视角.png' % (VKEY, VNAME))

# ---------- cut model at z=CUT for top + axo ----------
removed = clamped = 0
for ob in list(bpy.data.objects):
    if ob.type != 'MESH':
        continue
    me = ob.data
    zsv = [v.co.z for v in me.vertices]
    if min(zsv) >= CUT - 1e-9:
        bpy.data.objects.remove(ob, do_unlink=True)
        removed += 1
        continue
    if max(zsv) > CUT:
        for v in me.vertices:
            if v.co.z > CUT:
                v.co.z = CUT
        me.update()
        clamped += 1
print('[CUT] z=%.2f removed=%d clamped=%d' % (CUT, removed, clamped))

for ob in route_obs:
    ob.hide_render = False

render('top', '%s_%s_俯视.png' % (VKEY, VNAME))
render('axo', '%s_%s_轴测.png' % (VKEY, VNAME))

print('=== done %s ===' % VKEY)
