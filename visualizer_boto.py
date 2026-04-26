import sys
import os
import glob
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.patches as mpatches

# ── CONFIGURATION ────────────────────────────────────────────────────────────

def find_case_folder(hint=None):
    if hint:
        candidates = [f"./Case{hint}", f"Case{hint}"]
    else:
        candidates = sorted(glob.glob("./Case*")) + sorted(glob.glob("Case*"))
    for c in candidates:
        if os.path.isfile(os.path.join(c, "warehouse.csv")):
            return c
    if os.path.isfile("warehouse.csv"):
        return "."
    raise FileNotFoundError("No Case folder with warehouse.csv found.")

def find_best_output_csv():
    for name in ["best_output.csv", "layout_final.csv", "result.csv", "solution.csv"]:
        if os.path.isfile(name):
            return name
    raise FileNotFoundError("No best_output.csv / layout_final.csv found.")

case_hint = sys.argv[1] if len(sys.argv) > 1 else None
CASE_DIR  = find_case_folder(case_hint)
print(f"Using folder: {CASE_DIR}")

# ── DATA LOADING ─────────────────────────────────────────────────────────────

pw = np.genfromtxt(os.path.join(CASE_DIR, "warehouse.csv"), delimiter=',')
if pw.ndim == 1:
    pw = pw.reshape(1, -1)

obs_path = os.path.join(CASE_DIR, "obstacles.csv")
try:
    obs_raw = np.genfromtxt(obs_path, delimiter=',')
    if obs_raw.ndim == 1:
        obs_raw = obs_raw.reshape(1, -1)
    if obs_raw.size == 0:
        obs_raw = np.empty((0, 4))
except Exception:
    obs_raw = np.empty((0, 4))

bays_path = os.path.join(CASE_DIR, "types_of_bays.csv")
df_bays = pd.read_csv(bays_path)
if 'Id' not in df_bays.columns:
    df_bays = pd.read_csv(bays_path, header=None,
                           names=['Id', 'W', 'D', 'H', 'G', 'nL', 'P'])
d_bays = df_bays.set_index('Id').to_dict('index')

# ── STEPPED CEILING ───────────────────────────────────────────────────────────
ceiling_path = os.path.join(CASE_DIR, "ceiling.csv")
try:
    h_data = np.genfromtxt(ceiling_path, delimiter=',')
    if h_data.ndim == 1:
        h_data = h_data.reshape(1, -1)
    print(f"Ceiling loaded: {ceiling_path}  shape={h_data.shape}")
except Exception as e:
    h_data = None
    print(f"  ⚠ Could not load ceiling.csv: {e}")

BEST_OUTPUT_CSV = find_best_output_csv()
layout_raw = pd.read_csv(BEST_OUTPUT_CSV)
print(f"Columns in {BEST_OUTPUT_CSV}: {list(layout_raw.columns)}")

# Normalize column names
COL_MAP = {}
for col in layout_raw.columns:
    low = col.strip().lower()
    if   low in ('id', 'type_id', 'bay_id', 'bayid'):   COL_MAP[col] = 'Id'
    elif low == 'x':                                      COL_MAP[col] = 'X'
    elif low == 'y':                                      COL_MAP[col] = 'Y'
    elif low in ('rotation','theta','theta_rad','angle',
                 'rotation_rad','rot'):                   COL_MAP[col] = 'Rotation_rad'
    elif low in ('theta_deg','angle_deg','rotation_deg'): COL_MAP[col] = 'Rotation_deg'
    elif low == 'gapangle_deg':                           COL_MAP[col] = 'GapAngle_deg'
    elif low == 'gapface':                                COL_MAP[col] = 'GapFace'

layout = layout_raw.rename(columns=COL_MAP)

if 'Rotation_rad' in layout.columns:
    layout['Rotation'] = layout['Rotation_rad']
elif 'Rotation_deg' in layout.columns:
    layout['Rotation'] = np.deg2rad(layout['Rotation_deg'])
else:
    remaining = [c for c in layout.columns if c not in ('Id','X','Y')]
    first_col = remaining[0]
    max_val = layout[first_col].abs().max()
    layout['Rotation'] = (layout[first_col] if max_val <= 7
                          else np.deg2rad(layout[first_col]))

has_gap_angle = 'GapAngle_deg' in layout.columns
print(f"Bays loaded: {len(layout)}  |  GapAngle_deg available: {has_gap_angle}")

# ── GEOMETRY ──────────────────────────────────────────────────────────────────

def get_body(x, y, theta, W, D):
    c, s = np.cos(theta), np.sin(theta)
    lx = np.array([-W/2,  W/2,  W/2, -W/2])
    ly = np.array([-D/2, -D/2,  D/2,  D/2])
    return np.column_stack([x + c*lx - s*ly,
                             y + s*lx + c*ly])

def get_gap_from_angle_centered(x, y, theta, gap_angle_deg, W, D, G):
    gap_angle_rad = np.deg2rad(gap_angle_deg)
    c_gap, s_gap = np.cos(gap_angle_rad), np.sin(gap_angle_rad)
    edge_x = x + c_gap * (D / 2.0)
    edge_y = y + s_gap * (D / 2.0)
    c_lat, s_lat = np.cos(theta), np.sin(theta)
    half_w = W / 2.0
    pts = np.array([
        [edge_x - c_lat * half_w,           edge_y - s_lat * half_w          ],
        [edge_x + c_lat * half_w,           edge_y + s_lat * half_w          ],
        [edge_x + c_lat * half_w + c_gap*G, edge_y + s_lat * half_w + s_gap*G],
        [edge_x - c_lat * half_w + c_gap*G, edge_y - s_lat * half_w + s_gap*G],
    ])
    return pts

def get_gap_fallback(x, y, theta, W, D, G):
    c, s = np.cos(theta), np.sin(theta)
    lx = np.array([-W/2,  W/2,  W/2, -W/2])
    ly = np.array([ D/2,  D/2,  D/2+G, D/2+G])
    return np.column_stack([x + c*lx - s*ly,
                             y + s*lx + c*ly])

def get_strict_ceiling_height(x_start, x_end, h_data):
    """
    Minimum stepped ceiling height in the range [x_start, x_end].
    h_data: Nx2 array → col0=X, col1=height.
    Segment i covers [x_i, x_{i+1}) with height h_i.
    """
    h_sorted = h_data[np.argsort(h_data[:, 0])]
    x_pts, h_pts = h_sorted[:, 0], h_sorted[:, 1]

    h_min = np.inf
    for i in range(len(x_pts)):
        seg_x0 = x_pts[i]
        seg_x1 = x_pts[i + 1] if i + 1 < len(x_pts) else np.inf
        seg_h  = h_pts[i]
        # Overlap with [x_start, x_end]
        if seg_x1 > x_start and seg_x0 < x_end:
            h_min = min(h_min, seg_h)

    return h_min if h_min < np.inf else float(h_pts[-1])

# ── COLORS ────────────────────────────────────────────────────────────────────

BASE_COLORS = ['#2ecc71','#3498db','#9b59b6','#e67e22',
               '#1abc9c','#e74c3c','#f39c12','#16a085']
all_type_ids = sorted(d_bays.keys())
COLORS = {tid: BASE_COLORS[i % len(BASE_COLORS)] for i, tid in enumerate(all_type_ids)}

# ── PLOT 1: FLOOR PLAN LAYOUT ─────────────────────────────────────────────────

fig, ax = plt.subplots(figsize=(14, 9))
ax.set_facecolor('#f8f9fa')

wall_closed = np.vstack([pw, pw[0]])
ax.fill(pw[:,0], pw[:,1], color='#eaf0fb', zorder=1)
ax.plot(wall_closed[:,0], wall_closed[:,1], color='#2c3e8c', linewidth=3, zorder=2)

for r in obs_raw:
    ax.add_patch(patches.Rectangle((r[0],r[1]),r[2],r[3],
                 facecolor='#e74c3c', alpha=0.65, zorder=6))

seen_types = set()
for _, row in layout.iterrows():
    tid       = int(row['Id'])
    x_pos     = float(row['X'])
    y_pos     = float(row['Y'])
    theta_rad = float(row['Rotation'])

    if tid not in d_bays:
        print(f"  ⚠ Type {tid} not found in types_of_bays, skipping.")
        continue

    s     = d_bays[tid]
    W, D, G = s['W'], s['D'], s['G']
    color = COLORS.get(tid, '#95a5a6')
    seen_types.add(tid)

    body = get_body(x_pos, y_pos, theta_rad, W, D)

    if has_gap_angle and not pd.isna(row.get('GapAngle_deg', float('nan'))):
        gap = get_gap_from_angle_centered(x_pos, y_pos, theta_rad,
                                          float(row['GapAngle_deg']), W, D, G)
    else:
        gap = get_gap_fallback(x_pos, y_pos, theta_rad, W, D, G)

    ax.add_patch(patches.Polygon(body, facecolor=color, edgecolor='black',
                                  alpha=0.85, linewidth=0.7, zorder=5))
    ax.add_patch(patches.Polygon(gap, facecolor='gold', alpha=0.35,
                                  linestyle='--', linewidth=0.8,
                                  edgecolor='goldenrod', zorder=4))

    cx, cy = np.mean(body[:,0]), np.mean(body[:,1])
    ax.text(cx, cy, str(tid), ha='center', va='center',
            fontsize=5.5, fontweight='bold', color='white', zorder=7)

    if has_gap_angle and not pd.isna(row.get('GapAngle_deg', float('nan'))):
        gap_rad = np.deg2rad(float(row['GapAngle_deg']))
        arrow_len = D * 0.35
        ax.annotate("", xy=(cx + np.cos(gap_rad)*arrow_len,
                             cy + np.sin(gap_rad)*arrow_len),
                    xytext=(cx, cy),
                    arrowprops=dict(arrowstyle="-|>", color='white', lw=1.2),
                    zorder=8)

legend_handles = [
    mpatches.Patch(facecolor=COLORS[tid],
                   label=f"Type {tid}  W={d_bays[tid]['W']} D={d_bays[tid]['D']} "
                         f"H={d_bays[tid]['H']}  nL={d_bays[tid]['nL']}  P={d_bays[tid]['P']}")
    for tid in sorted(seen_types)
]
legend_handles += [
    mpatches.Patch(facecolor='gold',    alpha=0.5, label='Gap (aisle)'),
    mpatches.Patch(facecolor='#e74c3c', alpha=0.6, label='Obstacle'),
]
ax.legend(handles=legend_handles, loc='upper right', fontsize=7.5, framealpha=0.9)

margin = max(pw[:,0].max()-pw[:,0].min(), pw[:,1].max()-pw[:,1].min()) * 0.04
ax.set_aspect('equal')
ax.set_xlim(pw[:,0].min()-margin, pw[:,0].max()+margin)
ax.set_ylim(pw[:,1].min()-margin, pw[:,1].max()+margin)
ax.set_xlabel("X (m)", fontsize=10)
ax.set_ylabel("Y (m)", fontsize=10)

eficiencia_total = sum(
    d_bays[int(r.Id)]['P'] / d_bays[int(r.Id)]['nL']
    for _, r in layout.iterrows()
    if int(r.Id) in d_bays
)
ax.set_title(
    f"Final Layout — {len(layout)} Bays  |  "
    f"Σ(P/nL) = {eficiencia_total:.1f}  |  ",
    fontsize=11
)
ax.grid(True, alpha=0.15)

plt.tight_layout()
plt.savefig("layout_final.png", dpi=150)
print("Image saved as layout_final.png")

# ── PLOT 2: FRONT PROFILE — STEPPED HEIGHT VALIDATION ─────────────────────────

if h_data is not None:
    h_sorted = h_data[np.argsort(h_data[:, 0])]
    x_techo  = h_sorted[:, 0]
    y_techo  = h_sorted[:, 1]

    fig2, ax2 = plt.subplots(figsize=(14, 6))
    ax2.set_facecolor('#f8f9fa')

    n_ok  = 0
    n_bad = 0

    for _, row in layout.iterrows():
        tid       = int(row['Id'])
        x_pos     = float(row['X'])
        y_pos     = float(row['Y'])
        theta_rad = float(row['Rotation'])

        if tid not in d_bays:
            continue

        s       = d_bays[tid]
        W, D, G = s['W'], s['D'], s['G']
        H_bay   = s['H']

        # X projection of the body footprint
        body    = get_body(x_pos, y_pos, theta_rad, W, D)
        x_start = float(np.min(body[:, 0]))
        x_end   = float(np.max(body[:, 0]))

        # Minimum stepped ceiling height over that X range
        h_limite = get_strict_ceiling_height(x_start, x_end, h_data)

        if H_bay <= h_limite:
            ax2.fill_between([x_start, x_end], 0, H_bay,
                             color='forestgreen', alpha=0.5,
                             edgecolor='darkgreen', linewidth=0.6)
            n_ok += 1
        else:
            ax2.fill_between([x_start, x_end], 0, H_bay,
                             color='red', alpha=0.35,
                             edgecolor='darkred', linewidth=0.6)
            print(f"  ⚠ Bay Type={tid}  X=[{x_start:.3f}, {x_end:.3f}]  "
                  f"H_bay={H_bay:.3f}  >  H_ceiling={h_limite:.3f}")
            n_bad += 1

    # Stepped ceiling line on top
    ax2.plot(x_techo, y_techo,
             color='black', linestyle='--', drawstyle='steps-post',
             linewidth=2, label='Warehouse Ceiling (Stepped Limit)', zorder=5)

    # Legend
    legend_ok   = mpatches.Patch(facecolor='forestgreen', alpha=0.6,
                                  edgecolor='darkgreen', label=f'Bay OK (≤ ceiling)  [{n_ok}]')
    legend_bad  = mpatches.Patch(facecolor='red', alpha=0.4,
                                  edgecolor='darkred',  label=f'Bay EXCEEDS ceiling  [{n_bad}]')
    legend_ceil = plt.Line2D([0], [0], color='black', linestyle='--',
                              linewidth=2, label='Warehouse Ceiling (Stepped Limit)')
    ax2.legend(handles=[legend_ceil, legend_ok, legend_bad],
               loc='upper right', fontsize=9, framealpha=0.9)

    # Plot limits
    all_x_starts = []
    all_x_ends   = []
    for _, row in layout.iterrows():
        tid = int(row['Id'])
        if tid not in d_bays:
            continue
        s = d_bays[tid]
        body = get_body(float(row['X']), float(row['Y']),
                        float(row['Rotation']), s['W'], s['D'])
        all_x_starts.append(float(np.min(body[:, 0])))
        all_x_ends.append(float(np.max(body[:, 0])))

    x_min_all = min(x_techo[0],  min(all_x_starts)) if all_x_starts else x_techo[0]
    x_max_all = max(x_techo[-1], max(all_x_ends))   if all_x_ends   else x_techo[-1]
    x_pad = (x_max_all - x_min_all) * 0.02

    ax2.set_xlim(x_min_all - x_pad, x_max_all + x_pad)
    ax2.set_ylim(0, y_techo.max() * 1.15)
    ax2.set_xlabel("X (m)", fontsize=10)
    ax2.set_ylabel("Height Z (m)", fontsize=10)
    ax2.set_title(
        f"Front View: Stepped Height Validation — "
        f"{n_ok} OK  |  {n_bad} EXCEED",
        fontsize=11
    )
    ax2.grid(alpha=0.25)

    plt.tight_layout()
    plt.savefig("height_check.png", dpi=150)
    print(f"Image saved as height_check.png  ({n_ok} OK, {n_bad} exceed ceiling)")

else:
    print("  ⚠ No ceiling data — skipping Plot 2.")

plt.show()