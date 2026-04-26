/*
 * MECALUX | HACKUPC 2026 — Warehouse Optimizer
 * Traducción exacta del script Python a C puro.
 *
 * Compilar:
 *   gcc -O2 -o warehouse_optimizer warehouse_optimizer.c -lm
 *
 * Ejecutar:
 *   ./warehouse_optimizer
 *
 * Espera los ficheros CSV en ./Case/:
 *   warehouse.csv, obstacles.csv, ceiling.csv, types_of_bays.csv
 *
 * Genera:
 *   result.csv  (Id, X, Y, Rotation_rad, GapAngle_deg, GapFace)
 */
#define _USE_MATH_DEFINES

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <process.h>   /* Windows: _getpid */
#include <float.h>

/* ══════════════════════════════════════════════════════════════════
 * CONSTANTES Y LÍMITES
 * ══════════════════════════════════════════════════════════════════ */

#define MAX_WAREHOUSE_PTS  512
#define MAX_OBSTACLES      128
#define MAX_BAY_TYPES      64
#define MAX_LAYOUT         256
#define MAX_CEILING_PTS    256
#define BIG_PEN            1e16
#define HUGE_PEN           1e25

/*
 * ESCAPE POR ÁNGULOS FINOS (15°)
 * ─────────────────────────────────────────────────────────────────
 * El optimizador trabaja SIEMPRE en ángulos ortogonales (0/90/180/270°).
 * Solo cuando lleva FINE_STALL_ITERS iteraciones consecutivas sin
 * mejorar la energía más de FINE_IMPROVEMENT_THRESHOLD, activa el
 * modo "fine-angle" durante FINE_ACTIVE_ITERS iteraciones y luego
 * vuelve al modo ortogonal.
 *
 * FINE_STALL_ITERS         → cuántas iters sin mejora antes de activar
 * FINE_IMPROVEMENT_THRESHOLD → mejora mínima que cuenta como "progreso"
 * FINE_ACTIVE_ITERS        → cuántas iters con ángulos finos se permiten
 * FINE_COOLDOWN_ITERS      → iters de bloqueo después de salir del modo fino
 *                            (evita que se reactive inmediatamente)
 */
#define FINE_STALL_ITERS           4000
#define FINE_IMPROVEMENT_THRESHOLD 1.0
#define FINE_ACTIVE_ITERS          500
#define FINE_COOLDOWN_ITERS        2000

/* ══════════════════════════════════════════════════════════════════
 * ESTRUCTURAS
 * ══════════════════════════════════════════════════════════════════ */

typedef struct { double x, y; } Vec2;

typedef struct {
    Vec2 v[8];
    int  n;
} Poly;

typedef struct {
    double x, y, w, d;
} Obstacle;

typedef struct {
    int    id;
    double W, D, H, G;
    int    nL;
    double P;
    double eff;
    double area;
} BayType;

typedef struct {
    int    id;
    double x, y, theta;
} Bay;

typedef struct {
    Vec2     warehouse[MAX_WAREHOUSE_PTS];
    int      n_warehouse;

    Obstacle obstacles[MAX_OBSTACLES];
    int      n_obstacles;

    Vec2     ceiling[MAX_CEILING_PTS];
    int      n_ceiling;

    BayType  bay_types[MAX_BAY_TYPES];
    int      n_bay_types;

    double   xmin, ymin, xmax, ymax;
} Env;

/* ══════════════════════════════════════════════════════════════════
 * GEOMETRÍA
 * ══════════════════════════════════════════════════════════════════ */

static int point_in_poly(Vec2 p, const Vec2 *verts, int n)
{
    int inside = 0;
    for (int i = 0, j = n - 1; i < n; j = i++) {
        double xi = verts[i].x, yi = verts[i].y;
        double xj = verts[j].x, yj = verts[j].y;
        if (((yi > p.y) != (yj > p.y)) &&
            (p.x < (xj - xi) * (p.y - yi) / (yj - yi) + xi))
            inside = !inside;
    }
    return inside;
}

static int poly_contained_in(const Poly *poly, const Vec2 *outer, int n_outer)
{
    for (int i = 0; i < poly->n; i++)
        if (!point_in_poly(poly->v[i], outer, n_outer))
            return 0;
    return 1;
}

static int segments_intersect(Vec2 a1, Vec2 a2, Vec2 b1, Vec2 b2)
{
    double d1x = a2.x - a1.x, d1y = a2.y - a1.y;
    double d2x = b2.x - b1.x, d2y = b2.y - b1.y;
    double cross = d1x * d2y - d1y * d2x;
    if (fabs(cross) < 1e-12) return 0;
    double dx = b1.x - a1.x, dy = b1.y - a1.y;
    double t = (dx * d2y - dy * d2x) / cross;
    double u = (dx * d1y - dy * d1x) / cross;
    return (t >= 0 && t <= 1 && u >= 0 && u <= 1);
}

static int polys_edges_intersect(const Poly *A, const Poly *B)
{
    for (int i = 0; i < A->n; i++) {
        Vec2 a1 = A->v[i], a2 = A->v[(i + 1) % A->n];
        for (int j = 0; j < B->n; j++) {
            Vec2 b1 = B->v[j], b2 = B->v[(j + 1) % B->n];
            if (segments_intersect(a1, a2, b1, b2)) return 1;
        }
    }
    return 0;
}

static int paths_intersect(const Poly *A, const Poly *B)
{
    for (int i = 0; i < A->n; i++)
        if (point_in_poly(A->v[i], B->v, B->n)) return 1;
    for (int i = 0; i < B->n; i++)
        if (point_in_poly(B->v[i], A->v, A->n)) return 1;
    return polys_edges_intersect(A, B);
}

static int bbox_overlap(const Poly *A, const Poly *B)
{
    double aminx=A->v[0].x, amaxx=A->v[0].x, aminy=A->v[0].y, amaxy=A->v[0].y;
    double bminx=B->v[0].x, bmaxx=B->v[0].x, bminy=B->v[0].y, bmaxy=B->v[0].y;
    for (int i=1;i<A->n;i++) {
        if (A->v[i].x<aminx) aminx=A->v[i].x; if (A->v[i].x>amaxx) amaxx=A->v[i].x;
        if (A->v[i].y<aminy) aminy=A->v[i].y; if (A->v[i].y>amaxy) amaxy=A->v[i].y;
    }
    for (int i=1;i<B->n;i++) {
        if (B->v[i].x<bminx) bminx=B->v[i].x; if (B->v[i].x>bmaxx) bmaxx=B->v[i].x;
        if (B->v[i].y<bminy) bminy=B->v[i].y; if (B->v[i].y>bmaxy) bmaxy=B->v[i].y;
    }
    return !(amaxx<bminx || bmaxx<aminx || amaxy<bminy || bmaxy<aminy);
}

static int paths_intersect_fast(const Poly *A, const Poly *B)
{
    if (!bbox_overlap(A, B)) return 0;
    return paths_intersect(A, B);
}

/* ══════════════════════════════════════════════════════════════════
 * GEOMETRÍA DE BAHÍA
 * ══════════════════════════════════════════════════════════════════ */

static void get_bay_polys(double x, double y, double theta,
                           double W, double D, double G,
                           Poly *rack, Poly *gap)
{
    double c = cos(theta), s = sin(theta);
    double lb[4][2] = {
        {-W/2, -D/2}, { W/2, -D/2}, { W/2,  D/2}, {-W/2,  D/2}
    };
    double lg[4][2] = {
        {-W/2, D/2}, { W/2, D/2}, { W/2, D/2+G}, {-W/2, D/2+G}
    };
    rack->n = 4; gap->n = 4;
    for (int i = 0; i < 4; i++) {
        rack->v[i].x = c*lb[i][0] - s*lb[i][1] + x;
        rack->v[i].y = s*lb[i][0] + c*lb[i][1] + y;
        gap->v[i].x  = c*lg[i][0] - s*lg[i][1] + x;
        gap->v[i].y  = s*lg[i][0] + c*lg[i][1] + y;
    }
}

static void obstacle_to_poly(const Obstacle *o, Poly *p)
{
    p->n = 4;
    p->v[0] = (Vec2){o->x,        o->y       };
    p->v[1] = (Vec2){o->x + o->w, o->y       };
    p->v[2] = (Vec2){o->x + o->w, o->y + o->d};
    p->v[3] = (Vec2){o->x,        o->y + o->d};
}

/* ══════════════════════════════════════════════════════════════════
 * TECHO
 * ══════════════════════════════════════════════════════════════════ */

static double obtener_h_limite_estricto(const Vec2 *ceiling, int n,
                                         double x_start, double x_end)
{
    int idx = 0;
    for (int i = 0; i < n; i++) {
        if (ceiling[i].x <= x_start) idx = i;
        else break;
    }
    double h_min = ceiling[idx].y;
    for (int i = 0; i < n; i++) {
        if (ceiling[i].x > x_start && ceiling[i].x <= x_end)
            if (ceiling[i].y < h_min) h_min = ceiling[i].y;
    }
    return h_min;
}

/* ══════════════════════════════════════════════════════════════════
 * EVALUACIÓN INDIVIDUAL
 * ══════════════════════════════════════════════════════════════════ */

static double evaluar_bay(const Bay *bay, const Env *env,
                           Poly *out_rack, Poly *out_gap)
{
    double pen = 0.0;
    const BayType *bt = NULL;
    for (int i = 0; i < env->n_bay_types; i++)
        if (env->bay_types[i].id == bay->id) { bt = &env->bay_types[i]; break; }
    if (!bt) return BIG_PEN;

    Poly rack, gap;
    get_bay_polys(bay->x, bay->y, bay->theta,
                  bt->W, bt->D, bt->G, &rack, &gap);

    if (out_rack) *out_rack = rack;
    if (out_gap)  *out_gap  = gap;

    if (!poly_contained_in(&rack, env->warehouse, env->n_warehouse) ||
        !poly_contained_in(&gap,  env->warehouse, env->n_warehouse))
        pen += BIG_PEN;

    for (int k = 0; k < env->n_obstacles; k++) {
        Poly obs;
        obstacle_to_poly(&env->obstacles[k], &obs);
        if (paths_intersect(&rack, &obs) || paths_intersect(&gap, &obs))
            pen += BIG_PEN;
    }

    double x_min_rack = rack.v[0].x, x_max_rack = rack.v[0].x;
    for (int i = 1; i < rack.n; i++) {
        if (rack.v[i].x < x_min_rack) x_min_rack = rack.v[i].x;
        if (rack.v[i].x > x_max_rack) x_max_rack = rack.v[i].x;
    }
    double h_lim = obtener_h_limite_estricto(env->ceiling, env->n_ceiling,
                                              x_min_rack, x_max_rack);
    if (bt->H > h_lim) pen += BIG_PEN;

    return pen;
}

/* ══════════════════════════════════════════════════════════════════
 * ENERGÍA GLOBAL
 * ══════════════════════════════════════════════════════════════════ */

static double calcular_energia_global(const Bay *layout, int n_layout,
                                       const Env *env,
                                       double *pen_indiv)
{
    if (n_layout == 0) return HUGE_PEN;

    double total_pen = 0.0, score_total = 0.0, area_cubierta = 0.0;
    Poly racks[MAX_LAYOUT], gaps[MAX_LAYOUT];
    double cx[MAX_LAYOUT], cy[MAX_LAYOUT], areas[MAX_LAYOUT];

    for (int i = 0; i < n_layout; i++) {
        double p = evaluar_bay(&layout[i], env, &racks[i], &gaps[i]);
        pen_indiv[i] = p;
        total_pen    += p;

        const BayType *bt = NULL;
        for (int k = 0; k < env->n_bay_types; k++)
            if (env->bay_types[k].id == layout[i].id) { bt = &env->bay_types[k]; break; }
        if (bt) {
            score_total   += bt->eff;
            area_cubierta += bt->area;
            areas[i]       = bt->area;
        } else {
            areas[i] = 0.0;
        }
        cx[i] = layout[i].x;
        cy[i] = layout[i].y;
    }

    double u_attr = 0.0;
    const double K_GRAVITY = 17.0;
    const double SIGMA     = 25000000.0;

    for (int i = 0; i < n_layout; i++) {
        for (int j = i + 1; j < n_layout; j++) {
            if (paths_intersect_fast(&racks[i], &racks[j]) ||
                paths_intersect_fast(&racks[i], &gaps[j])  ||
                paths_intersect_fast(&racks[j], &gaps[i])) {
                total_pen    += BIG_PEN;
                pen_indiv[i] += 1e15;
            }
            double dx = cx[i] - cx[j], dy = cy[i] - cy[j];
            double dist_sq   = dx*dx + dy*dy;
            double attraction = (areas[i]*areas[j] / 1e8)
                              * exp(-dist_sq / (2.0*SIGMA*SIGMA));
            u_attr -= K_GRAVITY * attraction;
        }
    }

    double E = score_total - (area_cubierta * 0.005) + u_attr;
    return E + total_pen;
}

/* ══════════════════════════════════════════════════════════════════
 * GENERADORES ALEATORIOS DE ÁNGULO
 *
 * rand_ortho()      → siempre 0 / 90 / 180 / 270°  (modo normal)
 * rand_fine_angle() → múltiplo de 15° cualquiera    (modo emergencia)
 *
 * NOTA: rand_fine_angle NO se llama nunca desde el bucle principal;
 * solo se invoca cuando fine_mode == 1.
 * ══════════════════════════════════════════════════════════════════ */

static double rand_ortho(void)
{
    static const double ortho[4] = {0.0, M_PI/2.0, M_PI, 3.0*M_PI/2.0};
    return ortho[rand() % 4];
}

/* 24 ángulos múltiplos de 15°, EXCLUYENDO los 4 ortogonales.
 * (Los ortogonales siguen cubiertos por rand_ortho en modo normal;
 *  en modo fino se barajan todos para no perder las posiciones buenas.) */
static double rand_fine_angle(void)
{
    /* 24 múltiplos de 15° en [0, 2π) */
    static const double fine[24] = {
        0.0,
        M_PI/12.0,          /* 15° */
        M_PI/6.0,           /* 30° */
        M_PI/4.0,           /* 45° */
        M_PI/3.0,           /* 60° */
        5.0*M_PI/12.0,      /* 75° */
        M_PI/2.0,           /* 90° */
        7.0*M_PI/12.0,      /* 105° */
        2.0*M_PI/3.0,       /* 120° */
        3.0*M_PI/4.0,       /* 135° */
        5.0*M_PI/6.0,       /* 150° */
        11.0*M_PI/12.0,     /* 165° */
        M_PI,               /* 180° */
        13.0*M_PI/12.0,     /* 195° */
        7.0*M_PI/6.0,       /* 210° */
        5.0*M_PI/4.0,       /* 225° */
        4.0*M_PI/3.0,       /* 240° */
        17.0*M_PI/12.0,     /* 255° */
        3.0*M_PI/2.0,       /* 270° */
        19.0*M_PI/12.0,     /* 285° */
        5.0*M_PI/3.0,       /* 300° */
        7.0*M_PI/4.0,       /* 315° */
        11.0*M_PI/6.0,      /* 330° */
        23.0*M_PI/12.0      /* 345° */
    };
    return fine[rand() % 24];
}

static double rand_uniform(double lo, double hi)
{
    return lo + (hi - lo) * ((double)rand() / RAND_MAX);
}

static int rand_bay_id(const Env *env)
{
    return env->bay_types[rand() % env->n_bay_types].id;
}

static int argmax_double(const double *arr, int n)
{
    int best = 0;
    for (int i = 1; i < n; i++)
        if (arr[i] > arr[best]) best = i;
    return best;
}

/* ══════════════════════════════════════════════════════════════════
 * MOTOR PRINCIPAL — optimizar_layout()
 *
 * Lógica de "escape por ángulos finos":
 *
 *   - iters_sin_mejora  : contador de iteraciones consecutivas sin
 *                         mejorar e_best en más de FINE_IMPROVEMENT_THRESHOLD
 *   - fine_mode         : 1 cuando el escape está activo
 *   - fine_iters_left   : cuántas iteraciones quedan en modo fino
 *   - fine_cooldown     : bloqueo post-escape para evitar reactivación
 *                         inmediata (el sistema debe "asentarse" primero)
 *
 * En modo fino, SOLO se modifica el ángulo del bay con mayor
 * penalización (o un bay aleatorio si no hay penalización).
 * Las acciones de birth/death siguen usando rand_ortho() para no
 * corromper la lógica principal con geometría no ortogonal.
 * ══════════════════════════════════════════════════════════════════ */

static int optimizar_layout(const Env *env,
                             Bay *layout_out,
                             int  max_layout)
{
    Bay    layout[MAX_LAYOUT];
    Bay    best_layout[MAX_LAYOUT];
    Bay    respaldo[MAX_LAYOUT];
    double pen_indiv[MAX_LAYOUT];
    double pen_indiv_new[MAX_LAYOUT];
    int    n = 0, n_best = 0;

    double e_actual = calcular_energia_global(layout, n, env, pen_indiv);
    double e_best   = e_actual;
    double temp     = 10000.0;

    /* ── Estado del escape por ángulos finos ─────────────────── */
    int iters_sin_mejora = 0;
    int fine_mode        = 0;
    int fine_iters_left  = 0;
    int fine_cooldown    = 0;
    /* ─────────────────────────────────────────────────────────── */

    printf("------------------------------------------------------------------\n");
    printf("%-8s | %-5s | %-15s | %-8s | %s\n",
           "ITER", "BAYS", "ENERGÍA", "TEMP", "MODO");
    printf("------------------------------------------------------------------\n");

    for (int iter = 0; iter < 100000; iter++) {

        /* ── Actualizar estado del escape ──────────────────────── */
        if (fine_cooldown > 0) fine_cooldown--;

        if (fine_mode) {
            fine_iters_left--;
            if (fine_iters_left <= 0) {
                fine_mode        = 0;
                fine_cooldown    = FINE_COOLDOWN_ITERS;
                iters_sin_mejora = 0;
                printf("  [FINO] Modo ángulos finos DESACTIVADO en iter %d\n", iter);
            }
        } else {
            /* ¿Activar modo fino? Solo si no hay cooldown activo */
            if (fine_cooldown == 0 &&
                iters_sin_mejora >= FINE_STALL_ITERS &&
                n > 0)
            {
                fine_mode       = 1;
                fine_iters_left = FINE_ACTIVE_ITERS;
                iters_sin_mejora = 0;
                printf("  [FINO] Modo ángulos finos ACTIVADO en iter %d  "
                       "(e=%.4e, %d bays)\n", iter, e_actual, n);
            }
        }
        /* ─────────────────────────────────────────────────────── */

        memcpy(respaldo, layout, n * sizeof(Bay));
        int n_respaldo = n;

        double acc = rand_uniform(0.0, 1.0);

        /* ══════════════════════════════════════════════════════
         * ACCIÓN
         *
         * Modo normal  → igual que antes (ortogonal)
         * Modo fino    → SOLO rota el bay más penalizado;
         *                no se añaden ni eliminan bays para
         *                no perder la configuración actual.
         * ══════════════════════════════════════════════════════ */
        if (fine_mode) {
            /* En modo fino solo rotamos — no birth/death */
            if (n > 0) {
                double max_pen = pen_indiv[0];
                for (int k = 1; k < n; k++)
                    if (pen_indiv[k] > max_pen) max_pen = pen_indiv[k];

                int idx = (max_pen > 0)
                          ? argmax_double(pen_indiv, n)
                          : (rand() % n);

                layout[idx].theta = rand_fine_angle();

                /* Pequeño desplazamiento adicional para salir de colisiones */
                if (rand_uniform(0, 1) < 0.3) {
                    layout[idx].x += rand_uniform(-20.0, 20.0);
                    layout[idx].y += rand_uniform(-20.0, 20.0);
                }
            }

        } else {
            /* ── Modo normal (idéntico al original) ── */
            if (acc < 0.65 && n < 180 && n < max_layout) {
                layout[n].id    = rand_bay_id(env);
                layout[n].x     = rand_uniform(env->xmin, env->xmax);
                layout[n].y     = rand_uniform(env->ymin, env->ymax);
                layout[n].theta = rand_ortho();
                n++;
            } else if (n > 0) {
                double max_pen = pen_indiv[0];
                for (int k = 1; k < n; k++)
                    if (pen_indiv[k] > max_pen) max_pen = pen_indiv[k];

                int idx = (max_pen > 0)
                          ? argmax_double(pen_indiv, n)
                          : (rand() % n);

                if (acc < 0.75) {
                    for (int k = idx; k < n - 1; k++)
                        layout[k] = layout[k + 1];
                    n--;
                } else {
                    if (rand_uniform(0, 1) < 0.8) {
                        layout[idx].x += rand_uniform(-60.0, 60.0);
                        layout[idx].y += rand_uniform(-60.0, 60.0);
                        if (rand_uniform(0, 1) < 0.2)
                            layout[idx].theta = rand_ortho();
                    } else {
                        layout[idx].id = rand_bay_id(env);
                    }
                }
            }
        }

        /* ── Evaluar ─────────────────────────────────────────── */
        double e_nueva = calcular_energia_global(layout, n, env, pen_indiv_new);

        int acepta = 0;
        if (e_nueva < e_actual) {
            acepta = 1;
        } else {
            double prob = exp(-fabs(e_nueva - e_actual) / temp);
            acepta = (rand_uniform(0, 1) < prob);
        }

        if (acepta) {
            e_actual = e_nueva;
            memcpy(pen_indiv, pen_indiv_new, n * sizeof(double));

            /* ── Actualizar mejor global e contador de estancamiento ── */
            if (e_nueva < e_best - FINE_IMPROVEMENT_THRESHOLD) {
                e_best = e_nueva;
                memcpy(best_layout, layout, n * sizeof(Bay));
                n_best = n;
                iters_sin_mejora = 0;   /* hubo mejora real → resetear */
            } else {
                if (!fine_mode) iters_sin_mejora++;
            }
        } else {
            memcpy(layout, respaldo, n_respaldo * sizeof(Bay));
            n = n_respaldo;
            if (!fine_mode) iters_sin_mejora++;
        }

        if (iter % 1000 == 0) {
            const char *modo = fine_mode ? "FINO " : "ortog";
            printf("%7d  | %4d | %14.4e | %7.1f | %s\n",
                   iter, n, e_actual, temp, modo);
        }

        temp *= 0.9998;
    }

    printf("------------------------------------------------------------------\n");
    printf("FINAL: %d bays  energía=%.4e  mejor_global=%.4e\n",
           n, e_actual, e_best);
    printf("------------------------------------------------------------------\n");

    /* Devolver el mejor layout encontrado globalmente */
    memcpy(layout_out, best_layout, n_best * sizeof(Bay));
    return n_best;
}

/* ══════════════════════════════════════════════════════════════════
 * CARGA DE CSV
 * ══════════════════════════════════════════════════════════════════ */

static int read_csv_doubles(const char *path, double *cols, int ncols, int max_rows)
{
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "No se pudo abrir: %s\n", path); return 0; }

    char line[1024];
    int  n = 0;

    if (!fgets(line, sizeof(line), f)) { fclose(f); return 0; }
    char *endp;
    strtod(line, &endp);
    int has_header = (endp == line || *endp == '\0' || *line == '\r' || *line == '\n');
    if (!has_header) {
        char *tok = strtok(line, ",\r\n");
        int col = 0;
        while (tok && col < ncols) {
            cols[n * ncols + col] = atof(tok);
            tok = strtok(NULL, ",\r\n");
            col++;
        }
        if (col == ncols) n++;
    }

    while (n < max_rows && fgets(line, sizeof(line), f)) {
        char *tok = strtok(line, ",\r\n");
        int col = 0;
        while (tok && col < ncols) {
            cols[n * ncols + col] = atof(tok);
            tok = strtok(NULL, ",\r\n");
            col++;
        }
        if (col == ncols) n++;
    }
    fclose(f);
    return n;
}

static int cmp_by_x(const void *a, const void *b)
{
    double xa = ((Vec2*)a)->x, xb = ((Vec2*)b)->x;
    return (xa < xb) ? -1 : (xa > xb) ? 1 : 0;
}

static Env cargar_entorno(void)
{
    Env env;
    memset(&env, 0, sizeof(env));

    {
        double buf[MAX_WAREHOUSE_PTS * 2];
        int n = read_csv_doubles("./Case/warehouse.csv", buf, 2, MAX_WAREHOUSE_PTS);
        env.n_warehouse = n;
        env.xmin = env.xmax = buf[0];
        env.ymin = env.ymax = buf[1];
        for (int i = 0; i < n; i++) {
            env.warehouse[i].x = buf[i*2];
            env.warehouse[i].y = buf[i*2+1];
            if (buf[i*2]   < env.xmin) env.xmin = buf[i*2];
            if (buf[i*2]   > env.xmax) env.xmax = buf[i*2];
            if (buf[i*2+1] < env.ymin) env.ymin = buf[i*2+1];
            if (buf[i*2+1] > env.ymax) env.ymax = buf[i*2+1];
        }
        printf("Almacén: %d vértices  x=[%.1f,%.1f] y=[%.1f,%.1f]\n",
               n, env.xmin, env.xmax, env.ymin, env.ymax);
    }

    {
        double buf[MAX_OBSTACLES * 4];
        int n = read_csv_doubles("./Case/obstacles.csv", buf, 4, MAX_OBSTACLES);
        env.n_obstacles = n;
        for (int i = 0; i < n; i++)
            env.obstacles[i] = (Obstacle){buf[i*4], buf[i*4+1], buf[i*4+2], buf[i*4+3]};
        printf("Obstáculos: %d\n", n);
    }

    {
        double buf[MAX_CEILING_PTS * 2];
        int n = read_csv_doubles("./Case/ceiling.csv", buf, 2, MAX_CEILING_PTS);
        env.n_ceiling = n;
        for (int i = 0; i < n; i++) {
            env.ceiling[i].x = buf[i*2];
            env.ceiling[i].y = buf[i*2+1];
        }
        qsort(env.ceiling, n, sizeof(Vec2), cmp_by_x);
        printf("Techo: %d puntos\n", n);
    }

    {
        double buf[MAX_BAY_TYPES * 7];
        int n = read_csv_doubles("./Case/types_of_bays.csv", buf, 7, MAX_BAY_TYPES);
        env.n_bay_types = n;
        for (int i = 0; i < n; i++) {
            env.bay_types[i].id   = (int)buf[i*7 + 0];
            env.bay_types[i].W    = buf[i*7 + 1];
            env.bay_types[i].D    = buf[i*7 + 2];
            env.bay_types[i].H    = buf[i*7 + 3];
            env.bay_types[i].G    = buf[i*7 + 4];
            env.bay_types[i].nL   = (int)buf[i*7 + 5];
            env.bay_types[i].P    = buf[i*7 + 6];
            env.bay_types[i].eff  = env.bay_types[i].P / env.bay_types[i].nL;
            env.bay_types[i].area = env.bay_types[i].W * env.bay_types[i].D;
        }
        printf("Tipos de bahía: %d\n", n);
    }

    return env;
}

/* ══════════════════════════════════════════════════════════════════
 * SALIDA CSV
 * ══════════════════════════════════════════════════════════════════ */

static void escribir_result(const Bay *layout, int n, const char *path)
{
    FILE *f = fopen(path, "w");
    if (!f) { fprintf(stderr, "Error abriendo %s para escritura\n", path); return; }
    fprintf(f, "Id,X,Y,Rotation,GapAngle_deg,GapFace\n");
    for (int i = 0; i < n; i++) {
        double gap_angle = layout[i].theta + M_PI / 2.0;
        while (gap_angle <  0)        gap_angle += 2*M_PI;
        while (gap_angle >= 2*M_PI)   gap_angle -= 2*M_PI;
        double deg = gap_angle * 180.0 / M_PI;

        const char *face;
        if      (deg <  45.0 || deg >= 315.0) face = "E";
        else if (deg < 135.0)                  face = "N";
        else if (deg < 225.0)                  face = "W";
        else                                   face = "S";

        fprintf(f, "%d,%.6f,%.6f,%.6f,%.2f,%s\n",
                layout[i].id, layout[i].x, layout[i].y,
                layout[i].theta, deg, face);
    }
    fclose(f);
    printf("Result escrito en %s (%d bays)\n", path, n);
}

/* ══════════════════════════════════════════════════════════════════
 * MAIN
 * ══════════════════════════════════════════════════════════════════ */

int main(void)
{
    srand((unsigned)time(NULL) ^ ((unsigned)_getpid() << 16));

    printf("╔══════════════════════════════════════════════════════╗\n");
    printf("║  MECALUX | HACKUPC 2026 — Warehouse Optimizer (C)    ║\n");
    printf("╚══════════════════════════════════════════════════════╝\n\n");

    Env env = cargar_entorno();
    printf("\n");

    Bay layout[MAX_LAYOUT];
    int n = optimizar_layout(&env, layout, MAX_LAYOUT);

    /* ── Factor Q final ─────────────────────────────────────── */
    double sum_P = 0.0, sum_nL = 0.0, area_cubierta = 0.0, warehouse_area = 0.0;

    for (int i = 0; i < env.n_warehouse; i++) {
        int j = (i + 1) % env.n_warehouse;
        warehouse_area += env.warehouse[i].x * env.warehouse[j].y
                        - env.warehouse[j].x * env.warehouse[i].y;
    }
    warehouse_area = fabs(warehouse_area) / 2.0;

    for (int i = 0; i < n; i++) {
        for (int k = 0; k < env.n_bay_types; k++) {
            if (env.bay_types[k].id == layout[i].id) {
                sum_P         += env.bay_types[k].P;
                sum_nL        += env.bay_types[k].nL;
                area_cubierta += env.bay_types[k].W * env.bay_types[k].D;
                break;
            }
        }
    }

    double R           = (warehouse_area > 0.0) ? (area_cubierta / warehouse_area) : 0.0;
    double coste_total = (sum_nL > 0.0) ? (sum_P / sum_nL) : 0.0;
    double Q           = pow(coste_total, 2.0 - R);

    printf("\n");
    printf("  Bays colocados               = %d\n",       n);
    printf("  Suma P                       = %.2f\n",      sum_P);
    printf("  Suma L                       = %.0f\n",      sum_nL);
    printf("  coste_total (Suma P/Suma nL) = %.6f\n",      coste_total);
    printf("  Área almacén                 = %.2f\n",      warehouse_area);
    printf("  Área cubierta                = %.2f\n",      area_cubierta);
    printf("  R (%% área cubierta)          = %.6f\n",     R);
    printf("  Q = %.6f\n",                                 Q);

    printf("\n");
    escribir_result(layout, n, "result.csv");

    return 0;
}