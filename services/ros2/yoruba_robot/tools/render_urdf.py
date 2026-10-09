# render_urdf.py — draw a URDF's visuals (boxes, cylinders, spheres) to a PNG
# from three angles, with no ROS and no GPU. Used to eyeball the robot model and
# for the tutorial screenshots.
#
#   xacro description/robot.urdf.xacro > /tmp/robot.urdf
#   python3 tools/render_urdf.py /tmp/robot.urdf docs/robot_model.png
#
# Handles what our model uses: fixed/continuous joints with translation-only
# origins (joint angles drawn at 0), and visual origins with rpy rotations.
import math
import sys
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                   # noqa: E402
import numpy as np                                                # noqa: E402


def rpy_matrix(r, p, y):
    cr, sr, cp, sp, cy, sy = (math.cos(r), math.sin(r), math.cos(p),
                              math.sin(p), math.cos(y), math.sin(y))
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rz = np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]])
    return rz @ ry @ rx


def origin(el):
    o = el.find("origin") if el is not None else None
    xyz = [float(v) for v in (o.get("xyz", "0 0 0") if o is not None else "0 0 0").split()]
    rpy = [float(v) for v in (o.get("rpy", "0 0 0") if o is not None else "0 0 0").split()]
    return np.array(xyz), rpy_matrix(*rpy)


def link_poses(root):
    """World pose (translation, rotation) of every link, joints at angle 0."""
    joints = {j.find("child").get("link"): j for j in root.findall("joint")}
    links = [l.get("name") for l in root.findall("link")]
    cache = {}

    def pose(name):
        if name in cache:
            return cache[name]
        j = joints.get(name)
        if j is None:
            cache[name] = (np.zeros(3), np.eye(3))
        else:
            pt, pr = pose(j.find("parent").get("link"))
            t, r = origin(j)
            cache[name] = (pt + pr @ t, pr @ r)
        return cache[name]
    return {n: pose(n) for n in links}


def colors(root):
    out = {}
    for m in root.findall("material"):
        c = m.find("color")
        if c is not None:
            out[m.get("name")] = [float(v) for v in c.get("rgba").split()]
    return out


def box_faces(size, t, r):
    sx, sy, sz = [s / 2 for s in size]
    corners = np.array([[x, y, z] for x in (-sx, sx) for y in (-sy, sy) for z in (-sz, sz)])
    pts = (r @ corners.T).T + t
    idx = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]
    return [[pts[i] for i in f] for f in idx]


def cylinder_faces(radius, length, t, r, n=28):
    ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ring = np.stack([radius * np.cos(ang), radius * np.sin(ang), np.zeros(n)], 1)
    top = (r @ (ring + [0, 0, length / 2]).T).T + t
    bot = (r @ (ring - [0, 0, length / 2]).T).T + t
    faces = [list(top), list(bot)]
    for i in range(n):
        k = (i + 1) % n
        faces.append([top[i], top[k], bot[k], bot[i]])
    return faces


def sphere_faces(radius, t, n=14):
    faces = []
    th = np.linspace(0, math.pi, n)
    ph = np.linspace(0, 2 * math.pi, 2 * n)
    for i in range(n - 1):
        for j in range(2 * n - 1):
            quad = []
            for a, b in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)):
                quad.append(t + radius * np.array([math.sin(th[a]) * math.cos(ph[b]),
                                                   math.sin(th[a]) * math.sin(ph[b]),
                                                   math.cos(th[a])]))
            faces.append(quad)
    return faces


def collect(root):
    poses, mats = link_poses(root), colors(root)
    polys = []
    for link in root.findall("link"):
        lt, lr = poses[link.get("name")]
        for vis in link.findall("visual"):
            vt, vr = origin(vis)
            t, r = lt + lr @ vt, lr @ vr
            m = vis.find("material")
            rgba = mats.get(m.get("name"), [0.5, 0.5, 0.5, 1]) if m is not None else [0.5] * 3 + [1]
            g = vis.find("geometry")[0]
            if g.tag == "box":
                faces = box_faces([float(v) for v in g.get("size").split()], t, r)
            elif g.tag == "cylinder":
                faces = cylinder_faces(float(g.get("radius")), float(g.get("length")), t, r)
            elif g.tag == "sphere":
                faces = sphere_faces(float(g.get("radius")), t)
            else:
                continue
            polys.append((faces, rgba))
    return polys


def view_basis(elev, azim):
    """Orthographic camera: returns (right, up, toward_viewer) unit vectors."""
    e, a = math.radians(elev), math.radians(azim)
    toward = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    up_hint = np.array([0, 0, 1.0]) if abs(elev) < 89 else np.array([1.0, 0, 0])
    right = np.cross(up_hint, toward)
    right /= np.linalg.norm(right)
    up = np.cross(toward, right)
    return right, up, toward


def draw(polys, ax, elev, azim, title):
    """Painter's algorithm per FACE (not per part): far faces first, so
    overlapping parts occlude correctly."""
    right, up, toward = view_basis(elev, azim)
    faces = []
    ground = [np.array(p) for p in ([-0.17, -0.12, 0], [0.09, -0.12, 0],
                                     [0.09, 0.12, 0], [-0.17, 0.12, 0])]
    if elev > 0:
        faces.append((-1e9, ground, (0.86, 0.86, 0.83, 1.0)))
    for fs, rgba in polys:
        for f in fs:
            pts = np.array(f)
            n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
            shade = 1.0
            if np.linalg.norm(n) > 1e-12:
                n = n / np.linalg.norm(n)
                shade = 0.62 + 0.38 * abs(float(n @ np.array([0.3, -0.4, 0.86])))
            c = tuple(min(1.0, ch * shade) for ch in rgba[:3]) + (1.0,)
            faces.append((float(pts.mean(0) @ toward), list(pts), c))
    faces.sort(key=lambda t: t[0])
    for _, pts, c in faces:
        xy = [(float(p @ right), float(p @ up)) for p in pts]
        ax.add_patch(plt.Polygon(xy, closed=True, facecolor=c,
                                 edgecolor=(0, 0, 0, 0.18), linewidth=0.3))
    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.margins(0.06)
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title(title, fontsize=11)


def main(src, dst):
    root = ET.parse(src).getroot()
    polys = collect(root)
    views = [(28, -140, "3/4 view (front-left)"),
             (0, -90, "side view (front = right)"),
             (0, 0, "front view"),
             (90, -90, "top view (front = up)")]
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.6))
    for ax, (e, a, t) in zip(axes, views):
        draw(polys, ax, e, a, t)
    fig.tight_layout()
    fig.savefig(dst, dpi=110, facecolor="white")
    print(f"wrote {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
