# render_arena.py — top-down picture of ARENA (yoruba_robot/arena.py) with the
# robot at its start pose, for docs.  python3 tools/render_arena.py out.png
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                    # noqa: E402
from matplotlib.patches import Circle, FancyArrow, Rectangle       # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from yoruba_robot.arena import ARENA, walls                        # noqa: E402


def main(dst):
    a = ARENA
    fig, ax = plt.subplots(figsize=(6.4, 6.4))
    ax.add_patch(Rectangle((-a["size_x"] / 2, -a["size_y"] / 2), a["size_x"], a["size_y"],
                           color=a["floor_color"]))
    for w in walls(a):
        (x, y, _), (sx, sy, _) = w["xyz"], w["size"]
        ax.add_patch(Rectangle((x - sx / 2, y - sy / 2), sx, sy, color=a["wall_color"]))
    for o in a["obstacles"]:
        x, y = o["xy"]
        if o["shape"] == "box":
            sx, sy = o["size"][:2]
            ax.add_patch(Rectangle((x - sx / 2, y - sy / 2), sx, sy, color=o["color"]))
        else:
            ax.add_patch(Circle((x, y), o["size"][0], color=o["color"]))
        ax.text(x, y, o["name"], ha="center", va="center", fontsize=9, color="white",
                weight="bold")
    ax.add_patch(Circle((0, 0), a["start_clear_radius"], fill=False, ls="--",
                        color=(0.2, 0.6, 0.3)))
    # robot footprint (plate 0.20 x 0.12, axle at x=0, plate centre x=-0.045)
    ax.add_patch(Rectangle((-0.145, -0.06), 0.20, 0.12, color=(0.30, 0.62, 0.95)))
    ax.add_patch(Rectangle((-0.0325, 0.062), 0.065, 0.026, color="black"))
    ax.add_patch(Rectangle((-0.0325, -0.088), 0.065, 0.026, color="black"))
    ax.add_patch(FancyArrow(0.07, 0, 0.25, 0, width=0.02, color=(0.2, 0.6, 0.3)))
    ax.text(0.0, -0.22, "start (0, 0)\nfacing +x", ha="center", fontsize=8)
    m = a["size_x"] / 2 + 0.15
    ax.set_xlim(-m, m)
    ax.set_ylim(-m, m)
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)  forward ->")
    ax.set_ylabel("y (m)  left ->")
    ax.set_title(f"arena: {a['size_x']} x {a['size_y']} m (odom frame)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(dst, dpi=100)
    print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv[1])
