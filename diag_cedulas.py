"""Menu interactivo de diagnostico y acciones sobre cedulas SIAN -> Policia.

Usa un archivo local de configuracion (`db_config.py`) para abrir las
conexiones psycopg2 por demanda. El archivo real con credenciales queda fuera
del repo (ver `db_config.example.py` para el esqueleto).

Uso:
    python diag_cedulas.py                  # PROD por default
    python diag_cedulas.py --test 1         # TEST

Cada accion correctiva muestra un PREVIEW (SELECT) antes de aplicar el
INSERT/UPDATE/DELETE y pide confirmacion explicita escribiendo 'c'. Cualquier
otra cosa hace ROLLBACK.
"""
import argparse
import builtins
import io
import sys
import textwrap
import traceback
from contextlib import redirect_stdout
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog, ttk

import psycopg2
import psycopg2.extras


NEON_BG = "#000000"
NEON_FG = "#00ff66"
NEON_ACCENT = "#00cc55"
BUTTON_BG = "#b3b3b3"
BUTTON_FG = "#000000"


def apply_neon_palette(widget):
    """Aplica paleta negro/verde a widgets Tk clasicos recursivamente."""
    try:
        if isinstance(widget, tk.Button):
            widget.configure(
                bg=BUTTON_BG,
                fg=BUTTON_FG,
                activebackground=BUTTON_BG,
                activeforeground=BUTTON_FG,
            )
        else:
            widget.configure(bg=NEON_BG, fg=NEON_FG, insertbackground=NEON_FG)
    except Exception:
        try:
            widget.configure(bg=NEON_BG)
        except Exception:
            pass
    for child in widget.winfo_children():
        apply_neon_palette(child)


def configure_ttk_neon(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=NEON_BG, foreground=NEON_FG, fieldbackground=NEON_BG)
    style.configure("Treeview", background=NEON_BG, foreground=NEON_FG, fieldbackground=NEON_BG)
    style.configure("Treeview.Heading", background=NEON_BG, foreground=NEON_FG)
    style.map("Treeview", background=[("selected", "#003311")], foreground=[("selected", NEON_FG)])
    style.configure("TScrollbar", background=NEON_BG, troughcolor=NEON_BG, arrowcolor=NEON_FG)

# Carga del archivo local con credenciales (no versionado).
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from db_config import DATABASES
except ImportError as exc:
    raise RuntimeError(
        "No se encontro db_config.py. Copia db_config.example.py a db_config.py "
        "y completa las credenciales."
    ) from exc


# ---------------------------------------------------------------------------
# Helpers de presentacion
# ---------------------------------------------------------------------------
def banner(titulo: str) -> None:
    line = "=" * max(60, len(titulo) + 4)
    print()
    print(line)
    print(f"  {titulo}")
    print(line)


def imprimir_tabla(rows, headers=None, max_col=60):
    """Imprime filas en formato tabla simple. Trunca celdas a max_col chars."""
    if not rows:
        print("  (sin resultados)")
        return
    if headers is None:
        if isinstance(rows[0], dict):
            headers = list(rows[0].keys())
        else:
            headers = [f"c{i}" for i in range(len(rows[0]))]

    def cell(v):
        if v is None:
            return ""
        s = str(v).replace("\n", " ").replace("\r", " ")
        return s if len(s) <= max_col else s[: max_col - 3] + "..."

    data = []
    for r in rows:
        if isinstance(r, dict):
            data.append([cell(r.get(h)) for h in headers])
        else:
            data.append([cell(r[i]) if i < len(r) else "" for i, _ in enumerate(headers)])

    widths = [len(h) for h in headers]
    for row in data:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))

    sep = "  ".join("-" * w for w in widths)
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print(sep)
    for row in data:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    print(f"({len(data)} fila(s))")


def pedir(prompt, default=None, requerido=False, tipo=str):
    """input() con default opcional y casteo a tipo. None si vacio y no requerido."""
    suf = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{prompt}{suf}: ").strip()
        if not raw:
            if default is not None:
                raw = str(default)
            elif requerido:
                print("  >>> requerido, intenta de nuevo.")
                continue
            else:
                return None
        try:
            return tipo(raw)
        except (ValueError, TypeError):
            print(f"  >>> no es {tipo.__name__} valido.")


def confirmar(msg="confirma con 'c' (cualquier otra cosa = cancelar)") -> bool:
    return input(f"{msg}: ").strip().lower() == "c"


# ---------------------------------------------------------------------------
# Conexiones
# ---------------------------------------------------------------------------
class Conexiones:
    """Resuelve y cachea conexiones psycopg2 (DESTINO, ED, PANELNOTIFICACIONESWS e IURIXWEB)."""

    def __init__(self, test: bool):
        self.test = test
        self.sufijo = "TEST" if test else "PROD"
        cfg = DATABASES.get(self.sufijo, {})
        self._cfg_destino = cfg.get("DESTINO")
        self._cfg_ed = cfg.get("ED")
        self._cfg_panelws = cfg.get("PANELNOTIFICACIONESWS")
        self._cfg_iurixweb = cfg.get("IURIXWEB")

        if not self._cfg_destino:
            sys.exit(f"ERROR: falta DATABASES['{self.sufijo}']['DESTINO'] en db_config.py")

        if not self._cfg_ed:
            print(
                f"AVISO: falta DATABASES['{self.sufijo}']['ED'] en db_config.py "
                "-- las consultas contra ED van a fallar."
            )

        if not self._cfg_panelws:
            print(
                f"AVISO: falta DATABASES['{self.sufijo}']['PANELNOTIFICACIONESWS'] en db_config.py "
                "-- el CRUD de panelnotificacionesws usara DESTINO como fallback."
            )

        if not self._cfg_iurixweb:
            print(
                f"AVISO: falta DATABASES['{self.sufijo}']['IURIXWEB'] en db_config.py "
                "-- la opcion 'consultar iurix web' va a fallar."
            )

        destino_host = self._cfg_destino.get("host", "?")
        destino_db = self._cfg_destino.get("dbname") or self._cfg_destino.get("database", "?")
        print(f"[destino] {destino_host}/{destino_db} ({self.sufijo})")

        self._conn_destino = None
        self._conn_ed = None
        self._conn_panelws = None
        self._conn_iurixweb = None

    def destino(self):
        if self._conn_destino is None or self._conn_destino.closed:
            self._conn_destino = psycopg2.connect(**self._cfg_destino)
        return self._conn_destino

    def ed(self):
        if not self._cfg_ed:
            raise RuntimeError("BD ED no configurada para este sufijo")
        if self._conn_ed is None or self._conn_ed.closed:
            self._conn_ed = psycopg2.connect(**self._cfg_ed)
        return self._conn_ed


    def panelws(self):
        if not self._cfg_panelws:
            return self.destino()
        if self._conn_panelws is None or self._conn_panelws.closed:
            self._conn_panelws = psycopg2.connect(**self._cfg_panelws)
        return self._conn_panelws

    def iurixweb(self):
        if not self._cfg_iurixweb:
            raise RuntimeError("BD IURIXWEB no configurada para este sufijo")
        if self._conn_iurixweb is None or self._conn_iurixweb.closed:
            self._conn_iurixweb = psycopg2.connect(**self._cfg_iurixweb)
        return self._conn_iurixweb

    def cerrar(self):
        for c in (self._conn_destino, self._conn_ed, self._conn_panelws, self._conn_iurixweb):
            try:
                if c and not c.closed:
                    c.close()
            except Exception:
                pass


def fetchall_dict(conn, sql, params=None):
    """Helper: ejecuta SELECT y devuelve list[dict] con autocommit por seguridad."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def fetchone_dict(conn, sql, params=None):
    """Helper: ejecuta SELECT y devuelve una sola fila como dict (o None)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or {})
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Operaciones de busqueda
# ---------------------------------------------------------------------------
def op_buscar_cedula(conx: Conexiones):
    banner("BUSCAR CEDULA")
    print("Dejá vacíos los criterios que no quieras filtrar.")
    act           = pedir("act_id (pactuacionid)", tipo=int)
    act_externo   = pedir("ecednpoliciaidexterno (902...)", tipo=str)
    exp_num       = pedir("exp_numero", tipo=int)
    exp_anio      = pedir("exp_anio", tipo=int)
    exp_suf       = pedir("exp_sufijo", tipo=int)
    dac_cod       = pedir("dac_cod (CEDPOL/CEDCIT/CEDCON/CEDURG)", tipo=str)
    desde         = pedir("desde (YYYY-MM-DD)", tipo=str)
    hasta         = pedir("hasta (YYYY-MM-DD)", tipo=str)

    sql = """
        SELECT pmovimientoid, pactuacionid, pdomicilioelectronicopj,
               pdocumentotipoabreviatura, pnumero, panio, psufijo,
               ecednpoliciaidexterno, parchivoactnombre, ecednpoliciadesccausa,
               fechacreacion, perror, penviocedulanotificacionexito,
               penviocedulanotificacionfechahora,
               codigoseguimientomp, descartada, pdac_codigo, pdestinatario
        FROM enviocedulanotificacionpolicia
        WHERE (%(act)s          IS NULL OR pactuacionid              = %(act)s)
          AND (%(act_externo)s  IS NULL OR ecednpoliciaidexterno     = %(act_externo)s)
          AND (%(exp_num)s      IS NULL OR pnumero                   = %(exp_num)s)
          AND (%(exp_anio)s     IS NULL OR panio                     = %(exp_anio)s)
          AND (%(exp_suf)s      IS NULL OR psufijo                   = %(exp_suf)s)
          AND (%(dac_cod)s      IS NULL OR pdac_codigo               = %(dac_cod)s)
          AND (%(desde)s        IS NULL OR fechacreacion::date      >= %(desde)s::date)
          AND (%(hasta)s        IS NULL OR fechacreacion::date      <= %(hasta)s::date)
        ORDER BY fechacreacion DESC, pactuacionid DESC
        LIMIT 50;
    """
    rows = fetchall_dict(conx.destino(), sql, dict(
        act=act, act_externo=act_externo, exp_num=exp_num, exp_anio=exp_anio,
        exp_suf=exp_suf, dac_cod=dac_cod, desde=desde, hasta=hasta,
    ))
    imprimir_tabla(rows)


def op_estado_end2end(conx: Conexiones):
    banner("ESTADO END-TO-END")
    act = pedir("act_id (pactuacionid) [requerido]", tipo=int, requerido=True)
    sql = """
        SELECT pactuacionid, ecednpoliciaidexterno, fechacreacion,
               laststagesian, finsian, qrexitoso,
               perror, penviocedulanotificacionexito, penviocedulanotificacionfechahora,
               codigoseguimientomp, ecedarchivosegnotid,
               fconstanciarec, tieneconstancia, fenviadaiw, feiw,
               descartada, fechadescarte, usuariodescarte, motivo_id, fechalaststate
        FROM enviocedulanotificacionpolicia
        WHERE pactuacionid = %(act)s
        ORDER BY fechacreacion DESC;
    """
    rows = fetchall_dict(conx.destino(), sql, dict(act=act))
    imprimir_tabla(rows)


def op_atascadas(conx: Conexiones):
    banner("CEDULAS ATASCADAS (no descartadas, finsian=false)")
    desde = pedir("desde (YYYY-MM-DD)", tipo=str)
    hasta = pedir("hasta (YYYY-MM-DD)", tipo=str)
    sql = """
        SELECT pactuacionid, ecednpoliciaidexterno, pdocumentotipoabreviatura,
               fechacreacion, laststagesian, finsian, qrexitoso, perror,
               codigoseguimientomp, fconstanciarec, feiw
        FROM enviocedulanotificacionpolicia
        WHERE COALESCE(descartada, false) = false
          AND COALESCE(finsian,    false) = false
          AND (%(desde)s IS NULL OR fechacreacion::date >= %(desde)s::date)
          AND (%(hasta)s IS NULL OR fechacreacion::date <= %(hasta)s::date)
        ORDER BY fechacreacion DESC
        LIMIT 100;
    """
    imprimir_tabla(fetchall_dict(conx.destino(), sql, dict(desde=desde, hasta=hasta)))


def op_huerfanas(conx: Conexiones):
    banner("CEDULAS HUERFANAS (uidgestor en ceros)")
    sql = """
        SELECT e.pactuacionid, e.ecednpoliciaidexterno, e.fechacreacion,
               cqr.uidgestor, cqr.uidgestorcedula, cqr.fechagenerado,
               e.codigoseguimientomp, e.descartada
        FROM enviocedulanotificacionpolicia e
        LEFT JOIN cedulasconcodigoqr cqr USING (pmovimientoid, pactuacionid, pdomicilioelectronicopj)
        WHERE COALESCE(TRIM(e.codigoseguimientomp), '') = ''
          AND COALESCE(e.descartada, false) = false
          AND (cqr.uidgestor       IS NULL
               OR cqr.uidgestor       = '00000000-0000-0000-0000-000000000000'
               OR cqr.uidgestorcedula IS NULL
               OR cqr.uidgestorcedula = '00000000-0000-0000-0000-000000000000')
        ORDER BY e.fechacreacion DESC
        LIMIT 50;
    """
    imprimir_tabla(fetchall_dict(conx.destino(), sql))


def op_logs(conx: Conexiones):
    banner("LOGS de la cedula")
    act = pedir("act_id (pactuacionid) [requerido]", tipo=int, requerido=True)

    print("\n--- sianlogsvarios ---")
    imprimir_tabla(fetchall_dict(conx.destino(), """
        SELECT sianlogsvariosid, sianlogsvariosfecha, proceso, reintentar,
               codigoseguimientomp,
               LEFT(sianlogsvariosdetalle, 300) AS detalle_head
        FROM sianlogsvarios
        WHERE pactuacionid = %(act)s
        ORDER BY sianlogsvariosfecha DESC
        LIMIT 100;
    """, dict(act=act)))

    print("\n--- logsenviocedulapolicia ---")
    imprimir_tabla(fetchall_dict(conx.destino(), """
        SELECT logsenviocedulapoliciaid, logfecha, logestado,
               LEFT(logsxmlrespuesta, 300) AS xml_head
        FROM logsenviocedulapolicia
        WHERE pactuacionid = %(act)s
        ORDER BY logfecha DESC NULLS LAST, logsenviocedulapoliciaid DESC
        LIMIT 100;
    """, dict(act=act)))


def op_historico_mp(conx: Conexiones):
    banner("HISTORIAL MP (notpolhistoricomp)")
    act = pedir("act_id (pactuacionid)", tipo=int)
    cod = pedir("codigoseguimientomp", tipo=str)
    sql = """
        SELECT notpolhistoricompestadonid, notpolhistoricompfecha,
               notpolhistoricompestado, notpolhistoricompmotivo,
               notpolhistoricompresponsable, notpolhistoricompdependencia,
               codigoseguimientomp, pactuacionid,
               LEFT(COALESCE(notpolhistoricompobservaciones,''), 200) AS obs_head
        FROM notpolhistoricomp
        WHERE (%(act)s IS NULL OR pactuacionid       = %(act)s)
          AND (%(cod)s IS NULL OR codigoseguimientomp = %(cod)s)
        ORDER BY notpolhistoricompfecha DESC NULLS LAST
        LIMIT 50;
    """
    imprimir_tabla(fetchall_dict(conx.destino(), sql, dict(act=act, cod=cod)))


def op_estado_ed(conx: Conexiones):
    banner("ESTADO EN BD ORIGEN ED (act + uje_act + dac + exp)")
    act      = pedir("act_id", tipo=int)
    exp_num  = pedir("exp_numero", tipo=int)
    exp_anio = pedir("exp_anio", tipo=int)
    exp_suf  = pedir("exp_sufijo", tipo=int)
    sql = """
        SELECT a.act_id, a.act_numero, a.act_exp_exp_id, a.act_fecfir, a.act_fec_aud,
               a.eact_id_act, a.act_estrecep, a.act_pdf_id AS guid_actuacion_pdf,
               a.act_idrel, ua.es_enotif, ua.uje_act_per_id,
               ua.destino_notif, ua.dir_notif,
               d.dac_cod, d.dac_descr,
               e.exp_id, e.exp_numero, e.exp_anio, e.exp_sufijo, e.exp_fecreg,
               LEFT(COALESCE(e.exp_carat, ''), 80) AS caratula_head
        FROM act a
        LEFT JOIN uje_act ua ON a.act_id = ua.uje_act_act_id AND a.act_exp_exp_id = ua.exp_id
        LEFT JOIN dac d      ON a.act_dact_id = d.dac_id
        LEFT JOIN exp e      ON a.act_exp_exp_id = e.exp_id
        WHERE (%(act)s      IS NULL OR a.act_id     = %(act)s)
          AND (%(exp_num)s  IS NULL OR e.exp_numero = %(exp_num)s)
          AND (%(exp_anio)s IS NULL OR e.exp_anio   = %(exp_anio)s)
          AND (%(exp_suf)s  IS NULL OR e.exp_sufijo = %(exp_suf)s)
        LIMIT 50;
    """
    try:
        imprimir_tabla(fetchall_dict(conx.ed(), sql, dict(
            act=act, exp_num=exp_num, exp_anio=exp_anio, exp_suf=exp_suf,
        )))
    except Exception as e:
        print(f"ERROR contra BD ED: {e}")


def op_stats(conx: Conexiones):
    banner("STATS por dia / dac_cod / perror")
    desde = pedir("desde (YYYY-MM-DD)", tipo=str)
    hasta = pedir("hasta (YYYY-MM-DD)", tipo=str)
    sql = """
        SELECT fechacreacion::date AS dia,
               pdac_codigo,
               COUNT(*) AS total,
               SUM(CASE WHEN COALESCE(TRIM(codigoseguimientomp),'')<>'' THEN 1 ELSE 0 END) AS tomadas_mp,
               SUM(CASE WHEN COALESCE(descartada,false)=true THEN 1 ELSE 0 END) AS descartadas,
               SUM(CASE WHEN perror='EXITO'    THEN 1 ELSE 0 END) AS exito,
               SUM(CASE WHEN perror='ERROR'    THEN 1 ELSE 0 END) AS error,
               SUM(CASE WHEN perror='REENVIAR' THEN 1 ELSE 0 END) AS reenviar,
               SUM(CASE WHEN perror IS NULL    THEN 1 ELSE 0 END) AS sin_estado
        FROM enviocedulanotificacionpolicia
        WHERE (%(desde)s IS NULL OR fechacreacion::date >= %(desde)s::date)
          AND (%(hasta)s IS NULL OR fechacreacion::date <= %(hasta)s::date)
        GROUP BY 1, 2
        ORDER BY 1 DESC, 2;
    """
    imprimir_tabla(fetchall_dict(conx.destino(), sql, dict(desde=desde, hasta=hasta)))


# ---------------------------------------------------------------------------
# Acciones (con preview + confirmacion explicita)
# ---------------------------------------------------------------------------
def _preview_y_aplicar(conn, sql_preview, sql_apply, params, sql_post=None,
                       descripcion="accion"):
    """Patron comun: SELECT preview -> mostrar -> pedir 'c' -> aplicar -> SELECT post."""
    print("\n--- PREVIEW (lo que se va a tocar) ---")
    rows = fetchall_dict(conn, sql_preview, params)
    imprimir_tabla(rows)
    if not rows:
        print(">>> nada que cambiar; volviendo al menu.")
        return
    if not confirmar(f"\nAplicar {descripcion}? Escribi 'c' para CONFIRMAR"):
        print(">>> cancelado.")
        return
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql_apply, params)
            print(f">>> filas afectadas: {cur.rowcount}")
        if sql_post:
            print("\n--- POST (estado luego de aplicar) ---")
            imprimir_tabla(fetchall_dict(conn, sql_post, params))
        conn.commit()
        print(f">>> COMMIT OK -- {descripcion}")
    except Exception as e:
        conn.rollback()
        print(f"ERROR -- ROLLBACK aplicado: {e}")


def acc_descartar(conx: Conexiones):
    banner("A1) DESCARTAR cedula")
    act          = pedir("act_id [requerido]", tipo=int, requerido=True)
    pmovimiento  = pedir("pmovimientoid (opcional)", tipo=int)
    domicilio_pj = pedir("pdomicilioelectronicopj (opcional)", tipo=str)
    usuario      = pedir("usuariodescarte (smallint)", tipo=int, default=0)
    motivo       = pedir("motivo_id (smallint, opcional)", tipo=int)

    sel = """
        SELECT pactuacionid, pdomicilioelectronicopj, pdocumentotipoabreviatura,
               descartada, fechadescarte, usuariodescarte, motivo_id
        FROM enviocedulanotificacionpolicia
        WHERE pactuacionid = %(act)s
          AND (%(pmov)s   IS NULL OR pmovimientoid       = %(pmov)s)
          AND (%(dom)s    IS NULL OR pdomicilioelectronicopj = %(dom)s);
    """
    upd = """
        UPDATE enviocedulanotificacionpolicia
           SET descartada=true, fechadescarte=NOW(),
               usuariodescarte=%(usr)s, motivo_id=%(motivo)s
         WHERE pactuacionid = %(act)s
           AND (%(pmov)s IS NULL OR pmovimientoid       = %(pmov)s)
           AND (%(dom)s  IS NULL OR pdomicilioelectronicopj = %(dom)s);
    """
    params = dict(act=act, pmov=pmovimiento, dom=domicilio_pj,
                  usr=usuario, motivo=motivo)
    _preview_y_aplicar(conx.destino(), sel, upd, params, sql_post=sel,
                       descripcion="DESCARTAR cedula")


def acc_rehabilitar(conx: Conexiones):
    banner("A2) RE-HABILITAR cedula descartada")
    act = pedir("act_id [requerido]", tipo=int, requerido=True)
    sel = """
        SELECT pactuacionid, descartada, fechadescarte, motivo_id,
               codigoseguimientomp, perror, penviocedulanotificacionexito
        FROM enviocedulanotificacionpolicia
        WHERE pactuacionid = %(act)s;
    """
    upd = """
        UPDATE enviocedulanotificacionpolicia
           SET descartada=false, fechadescarte=NULL,
               usuariodescarte=NULL, motivo_id=NULL,
               codigoseguimientomp=NULL, perror=NULL,
               penviocedulanotificacionexito=false
         WHERE pactuacionid = %(act)s;
    """
    _preview_y_aplicar(conx.destino(), sel, upd, dict(act=act), sql_post=sel,
                       descripcion="RE-HABILITAR cedula")


def acc_forzar_estado(conx: Conexiones):
    banner("A2b) FORZAR estado via INSERT en logsenviocedulapolicia")
    print(textwrap.dedent("""
        Convencion logestado:
            0 -> EXITO    (perror='EXITO',    exito=TRUE)
            1 -> ERROR    (perror='ERROR',    exito=FALSE)
            2 -> REENVIAR (perror='REENVIAR', exito=FALSE)
        El INSERT en logsenviocedulapolicia gatilla el trigger
        trg_actualizar_enviocedulanotificacion que propaga a la cedula padre.
    """).strip())
    act          = pedir("act_id [requerido]", tipo=int, requerido=True)
    pmovimiento  = pedir("pmovimientoid [requerido]", tipo=int, requerido=True)
    domicilio_pj = pedir("pdomicilioelectronicopj [requerido]", tipo=str, requerido=True)
    logestado    = pedir("logestado (0/1/2)", tipo=int, default=2)
    if logestado not in (0, 1, 2):
        print(">>> logestado invalido."); return
    xml = pedir("logsxmlrespuesta (texto)", tipo=str,
                default="forzado manualmente por diag_cedulas.py")

    sel = """
        SELECT pactuacionid, perror, penviocedulanotificacionexito,
               penviocedulanotificacionfechahora,
               LEFT(COALESCE(penviocedulanotificacionrespuestajson,''),200) AS resp_head
        FROM enviocedulanotificacionpolicia
        WHERE pactuacionid = %(act)s
          AND pmovimientoid       = %(pmov)s
          AND pdomicilioelectronicopj = %(dom)s;
    """
    ins = """
        INSERT INTO logsenviocedulapolicia
            (pmovimientoid, pactuacionid, pdomicilioelectronicopj,
             logsxmlrespuesta, logfecha, logestado)
        VALUES (%(pmov)s, %(act)s, %(dom)s, %(xml)s, CURRENT_DATE, %(est)s);
    """
    params = dict(act=act, pmov=pmovimiento, dom=domicilio_pj,
                  est=logestado, xml=xml)
    _preview_y_aplicar(conx.destino(), sel, ins, params, sql_post=sel,
                       descripcion=f"FORZAR estado logestado={logestado}")


def acc_parche_uid(conx: Conexiones):
    banner("A4) PARCHE uidgestor / uidgestorcedula")
    act      = pedir("act_id [requerido]", tipo=int, requerido=True)
    nuevo_a  = pedir("nuevo uidgestor (UUID actuacion, vacio = no tocar)", tipo=str)
    nuevo_c  = pedir("nuevo uidgestorcedula (UUID cedula, vacio = no tocar)", tipo=str)
    if not nuevo_a and not nuevo_c:
        print(">>> nada para parchear."); return
    sel = """
        SELECT pactuacionid, pdomicilioelectronicopj,
               uidgestor, uidgestorcedula, fechagenerado
        FROM cedulasconcodigoqr
        WHERE pactuacionid = %(act)s;
    """
    upd = """
        UPDATE cedulasconcodigoqr
           SET uidgestor       = COALESCE(%(uid_a)s, uidgestor),
               uidgestorcedula = COALESCE(%(uid_c)s, uidgestorcedula)
         WHERE pactuacionid = %(act)s;
    """
    _preview_y_aplicar(conx.destino(), sel, upd,
                       dict(act=act, uid_a=nuevo_a, uid_c=nuevo_c),
                       sql_post=sel, descripcion="PARCHE uidgestor")


def acc_nota_sianlog(conx: Conexiones):
    banner("A5) INSERTAR nota en sianlogsvarios")
    act          = pedir("act_id [requerido]", tipo=int, requerido=True)
    pmovimiento  = pedir("pmovimientoid [requerido]", tipo=int, requerido=True)
    domicilio_pj = pedir("pdomicilioelectronicopj [requerido]", tipo=str, requerido=True)
    proceso      = pedir("proceso", tipo=str, default="manual")
    detalle      = pedir("detalle (texto)", tipo=str,
                         default="nota manual desde diag_cedulas.py")
    sel = "SELECT 0 AS no_aplica;"   # no hay preview real para INSERT puro
    ins = """
        INSERT INTO sianlogsvarios
            (pactuacionid, pmovimientoid, pdomicilioelectronicopj,
             sianlogsvariosdetalle, sianlogsvariosfecha, proceso, reintentar)
        VALUES (%(act)s, %(pmov)s, %(dom)s, %(det)s, NOW(), %(proc)s, 'NO');
    """
    post = """
        SELECT sianlogsvariosid, sianlogsvariosfecha, proceso, reintentar,
               LEFT(sianlogsvariosdetalle,200) AS detalle_head
        FROM sianlogsvarios
        WHERE pactuacionid = %(act)s
        ORDER BY sianlogsvariosfecha DESC LIMIT 5;
    """
    print("\n(no hay preview para INSERT puro; se mostrara el resultado post-insert)")
    if not confirmar("Confirmar INSERT?"):
        print(">>> cancelado."); return
    try:
        with conx.destino().cursor() as cur:
            cur.execute(ins, dict(act=act, pmov=pmovimiento, dom=domicilio_pj,
                                  det=detalle, proc=proceso))
        conx.destino().commit()
        print(">>> COMMIT OK")
        imprimir_tabla(fetchall_dict(conx.destino(), post, dict(act=act)))
    except Exception as e:
        conx.destino().rollback()
        print(f"ERROR -- ROLLBACK: {e}")


def acc_borrar_cedula(conx: Conexiones):
    banner("A3) BORRAR cedula y todas sus hijas (solo si MP NO la tomo)")
    act = pedir("act_id [requerido]", tipo=int, requerido=True)
    print("\nVista previa de filas que se van a tocar:")
    counts = fetchall_dict(conx.destino(), """
        SELECT 'adjuntospolicia' AS tabla, COUNT(*) AS rows
          FROM adjuntospolicia      WHERE pactuacionid = %(act)s
        UNION ALL
        SELECT 'cedulasconcodigoqr',     COUNT(*) FROM cedulasconcodigoqr     WHERE pactuacionid = %(act)s
        UNION ALL
        SELECT 'logsenviocedulapolicia', COUNT(*) FROM logsenviocedulapolicia WHERE pactuacionid = %(act)s
        UNION ALL
        SELECT 'enviocedulanotificacionpolicia', COUNT(*) FROM enviocedulanotificacionpolicia WHERE pactuacionid = %(act)s;
    """, dict(act=act))
    imprimir_tabla(counts)

    estado = fetchall_dict(conx.destino(), """
        SELECT pactuacionid, codigoseguimientomp, descartada
        FROM enviocedulanotificacionpolicia WHERE pactuacionid = %(act)s;
    """, dict(act=act))
    imprimir_tabla(estado)

    print("\nOJO: solo se borran las filas con codigoseguimientomp vacio Y descartada=false.")
    if not confirmar("Confirma DELETE?"):
        print(">>> cancelado."); return

    safe_filter = """
        SELECT pmovimientoid, pactuacionid, pdomicilioelectronicopj
          FROM enviocedulanotificacionpolicia
         WHERE pactuacionid = %(act)s
           AND COALESCE(TRIM(codigoseguimientomp),'') = ''
           AND COALESCE(descartada, false) = false
    """
    try:
        with conx.destino().cursor() as cur:
            for tbl in ("adjuntospolicia", "cedulasconcodigoqr", "logsenviocedulapolicia"):
                cur.execute(f"""
                    DELETE FROM {tbl}
                    WHERE (pmovimientoid, pactuacionid, pdomicilioelectronicopj) IN ({safe_filter});
                """, dict(act=act))
                print(f"  {tbl}: {cur.rowcount} fila(s) borrada(s)")

            cur.execute("""
                DELETE FROM enviocedulanotificacionpolicia
                WHERE pactuacionid = %(act)s
                  AND COALESCE(TRIM(codigoseguimientomp),'') = ''
                  AND COALESCE(descartada, false) = false;
            """, dict(act=act))
            print(f"  enviocedulanotificacionpolicia: {cur.rowcount} fila(s) borrada(s)")
        conx.destino().commit()
        print(">>> COMMIT OK")
    except Exception as e:
        conx.destino().rollback()
        print(f"ERROR -- ROLLBACK: {e}")


def acc_marcar_constancia(conx: Conexiones):
    banner("MARCAR / DES-MARCAR constancia recibida")
    print(textwrap.dedent("""
        El trigger trg_actualizarconstanciarec se dispara al UPDATE de
        ecedarchivosegnotid: si el nuevo valor > 0 setea fconstanciarec=true
        y tieneconstancia='SI'; si es NULL/0 los desmarca.
    """).strip())
    act       = pedir("act_id [requerido]", tipo=int, requerido=True)
    nuevo_seg = pedir("ecedarchivosegnotid (numero, vacio = poner NULL)", tipo=int)

    sel = """
        SELECT pactuacionid, ecedarchivosegnotid, fconstanciarec, tieneconstancia
        FROM enviocedulanotificacionpolicia WHERE pactuacionid = %(act)s;
    """
    upd = """
        UPDATE enviocedulanotificacionpolicia
           SET ecedarchivosegnotid = %(seg)s
         WHERE pactuacionid = %(act)s;
    """
    _preview_y_aplicar(conx.destino(), sel, upd,
                       dict(act=act, seg=nuevo_seg), sql_post=sel,
                       descripcion="UPDATE ecedarchivosegnotid (gatilla trigger)")


def acc_forzar_es_enotif_ed(conx: Conexiones):
    banner("B1) (BD ED) Forzar es_enotif=0 para reprocesar")
    act = pedir("act_id (uje_act_act_id) [requerido]", tipo=int, requerido=True)
    sel = """
        SELECT uje_act_act_id AS act_id, exp_id, es_enotif
        FROM uje_act WHERE uje_act_act_id = %(act)s;
    """
    upd = """
        UPDATE uje_act SET es_enotif = 0 WHERE uje_act_act_id = %(act)s;
    """
    try:
        _preview_y_aplicar(conx.ed(), sel, upd, dict(act=act), sql_post=sel,
                           descripcion="UPDATE uje_act.es_enotif=0 (BD ED)")
    except Exception as e:
        print(f"ERROR contra BD ED: {e}")




# ---------------------------------------------------------------------------
# CRUD panelnotificacionesws
# ---------------------------------------------------------------------------
def _parse_bool(v):
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "t", "true", "s", "si", "y", "yes"):
        return True
    if s in ("0", "f", "false", "n", "no"):
        return False
    return None


def op_crud_tablerospar(conx: Conexiones):
    banner("CRUD TABLEROSPAR")
    accion = (pedir("Accion [L=listar, C=crear, U=actualizar, D=borrar]", requerido=True) or "").upper()
    conn = conx.panelws()

    if accion == "L":
        grupo = pedir("par_grupo (opcional)")
        clave = pedir("par_clave (opcional)")
        rows = fetchall_dict(conn, """
            SELECT par_id, par_grupo, par_clave, par_subclave, par_tipo, par_ambiente, par_activo,
                   COALESCE(par_valor, par_valor_json::text) AS valor, par_descripcion
            FROM tablerospar
            WHERE (%(g)s IS NULL OR par_grupo = %(g)s)
              AND (%(c)s IS NULL OR par_clave ILIKE '%%' || %(c)s || '%%')
            ORDER BY par_grupo, par_clave, par_id
            LIMIT 200
        """, {"g": grupo, "c": clave})
        imprimir_tabla(rows)
    elif accion == "C":
        params = {
            "g": pedir("par_grupo", requerido=True),
            "c": pedir("par_clave", requerido=True),
            "s": pedir("par_subclave"),
            "t": (pedir("par_tipo [STRING/NUMBER/BOOLEAN/JSON/FLAG]", requerido=True) or "").upper(),
            "a": (pedir("par_ambiente [PROD/HOMO/DEV/TODOS]", default="TODOS") or "TODOS").upper(),
            "act": _parse_bool(pedir("par_activo [true/false]", default="true")),
            "desc": pedir("par_descripcion"),
            "u": pedir("par_usuario_alta", default="diag_cedulas"),
            "v": pedir("par_valor (para STRING/NUMBER/BOOLEAN)")
        }
        raw_json = pedir("par_valor_json (JSON texto, opcional)")
        params["vj"] = raw_json
        sql = """
            INSERT INTO tablerospar(par_grupo, par_clave, par_subclave, par_valor, par_valor_json, par_tipo,
                                     par_ambiente, par_activo, par_descripcion, par_usuario_alta)
            VALUES (%(g)s, %(c)s, %(s)s, %(v)s, %(vj)s::jsonb, %(t)s, %(a)s, %(act)s, %(desc)s, %(u)s)
            RETURNING par_id;
        """
        with conn.cursor() as cur:
            cur.execute(sql, params)
            new_id = cur.fetchone()[0]
        conn.commit()
        print(f"OK insertado par_id={new_id}")
    elif accion == "U":
        par_id = pedir("par_id", requerido=True, tipo=int)
        rows = fetchall_dict(conn, "SELECT * FROM tablerospar WHERE par_id=%(id)s", {"id": par_id})
        imprimir_tabla(rows)
        if not rows:
            return
        curr = rows[0]
        tipo = (pedir("par_tipo", default=curr["par_tipo"]) or curr["par_tipo"]).upper()
        val = pedir("par_valor", default=curr.get("par_valor"))
        val_json = pedir("par_valor_json", default=(curr.get("par_valor_json") and str(curr.get("par_valor_json"))))
        params = {
            "id": par_id,
            "g": pedir("par_grupo", default=curr["par_grupo"]),
            "c": pedir("par_clave", default=curr["par_clave"]),
            "s": pedir("par_subclave", default=curr.get("par_subclave")),
            "t": tipo,
            "a": (pedir("par_ambiente", default=curr["par_ambiente"]) or curr["par_ambiente"]).upper(),
            "act": _parse_bool(pedir("par_activo", default=str(curr["par_activo"]))),
            "desc": pedir("par_descripcion", default=curr.get("par_descripcion")),
            "um": pedir("par_usuario_modif", default="diag_cedulas"),
            "v": val,
            "vj": val_json,
        }
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE tablerospar
                   SET par_grupo=%(g)s, par_clave=%(c)s, par_subclave=%(s)s, par_valor=%(v)s,
                       par_valor_json=%(vj)s::jsonb, par_tipo=%(t)s, par_ambiente=%(a)s,
                       par_activo=%(act)s, par_descripcion=%(desc)s, par_fecha_modif=now(),
                       par_usuario_modif=%(um)s
                 WHERE par_id=%(id)s
            """, params)
        conn.commit()
        print("OK actualizado")
    elif accion == "D":
        par_id = pedir("par_id", requerido=True, tipo=int)
        _preview_y_aplicar(conn,
            "SELECT * FROM tablerospar WHERE par_id=%(id)s",
            "DELETE FROM tablerospar WHERE par_id=%(id)s",
            {"id": par_id},
            descripcion="DELETE tablerospar")
    else:
        print("Accion invalida")


def op_crud_simple(conx: Conexiones, table: str, pk_fields, editable_fields):
    banner(f"CRUD {table.upper()}")
    accion = (pedir("Accion [L=listar, C=crear, U=actualizar, D=borrar]", requerido=True) or "").upper()
    conn = conx.panelws()
    if accion == "L":
        rows = fetchall_dict(conn, f"SELECT * FROM {table} ORDER BY 1 LIMIT 200")
        imprimir_tabla(rows)
        return
    if accion == "C":
        params = {f: pedir(f, requerido=True) for f in editable_fields}
        cols = ", ".join(editable_fields)
        vals = ", ".join([f"%({f})s" for f in editable_fields])
        with conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({vals})")
        conn.commit()
        print("OK insertado")
        return
    if accion in ("U", "D"):
        where = []
        params = {}
        for f in pk_fields:
            params[f] = pedir(f, requerido=True)
            where.append(f"{f}=%({f})s")
        where_sql = " AND ".join(where)
        rows = fetchall_dict(conn, f"SELECT * FROM {table} WHERE {where_sql}", params)
        imprimir_tabla(rows)
        if not rows:
            return
        if accion == "U":
            current = rows[0]
            for f in editable_fields:
                if f in pk_fields:
                    continue
                params[f] = pedir(f, default=current.get(f))
            set_sql = ", ".join([f"{f}=%({f})s" for f in editable_fields if f not in pk_fields])
            with conn.cursor() as cur:
                cur.execute(f"UPDATE {table} SET {set_sql} WHERE {where_sql}", params)
            conn.commit()
            print("OK actualizado")
        else:
            _preview_y_aplicar(conn, f"SELECT * FROM {table} WHERE {where_sql}", f"DELETE FROM {table} WHERE {where_sql}", params, descripcion=f"DELETE {table}")


def op_buscar_secuser_relaciones(conx: Conexiones):
    banner("BUSCAR SECUSER + RELACIONES")
    user_id = pedir("secuserid (opcional)", tipo=int)
    username = pedir("secusername (opcional)")
    rows = fetchall_dict(conx.panelws(), """
        SELECT u.secuserid, u.secusername, u.secuserpassword,
               COALESCE(string_agg(DISTINCT r.secrolename, ', '), '') AS roles,
               COALESCE(string_agg(DISTINCT j.pdomicilioelectronicopj, ', '), '') AS juzgados,
               count(DISTINCT ur.secroleid) AS cant_roles,
               count(DISTINCT j.juzgadosxrolid) AS cant_juzgados_asociados
          FROM secuser u
          LEFT JOIN secuserrole ur ON ur.secuserid = u.secuserid
          LEFT JOIN secrole r ON r.secroleid = ur.secroleid
          LEFT JOIN juzgadosxrol j ON j.secroleid = r.secroleid
         WHERE (%(id)s IS NULL OR u.secuserid = %(id)s)
           AND (%(name)s IS NULL OR u.secusername ILIKE '%%' || %(name)s || '%%')
         GROUP BY u.secuserid, u.secusername, u.secuserpassword
         ORDER BY u.secuserid
         LIMIT 200
    """, {"id": user_id, "name": username})
    imprimir_tabla(rows)


def op_consultar_iurix_web(conx: Conexiones):
    banner("CONSULTAR IURIX WEB (SQL-ACT-VIO-SIAN)")
    row = fetchone_dict(conx.destino(), """
        SELECT parametroblobfile
        FROM parametro
        WHERE parametronombre = %s
        LIMIT 1
    """, ("SQL-ACT-VIO-SIAN",))
    if not row or not row.get("parametroblobfile"):
        print("No se encontro SQL en parametro.parametroblobfile para SQL-ACT-VIO-SIAN")
        return

    raw_sql = row["parametroblobfile"]
    if isinstance(raw_sql, memoryview):
        raw_sql = raw_sql.tobytes()
    if isinstance(raw_sql, (bytes, bytearray)):
        try:
            sql = raw_sql.decode("utf-8").strip()
        except UnicodeDecodeError:
            sql = raw_sql.decode("latin-1", errors="replace").strip()
    else:
        sql = str(raw_sql).strip()

    if not sql:
        print("La consulta almacenada esta vacia.")
        return

    print("SQL recuperado (SQL-ACT-VIO-SIAN):")
    print(textwrap.indent(sql, "  "))
    print("Ejecutando consulta almacenada en IURIXWEB...")
    rows = fetchall_dict(conx.iurixweb(), sql)
    imprimir_tabla(rows)

# ---------------------------------------------------------------------------
# Menu principal
# ---------------------------------------------------------------------------
MENU_PRINCIPAL = """
=== DIAGNOSTICO CEDULAS SIAN  ({sufijo}) ===
  1)  Buscar cedula (por act/externo/expediente/fechas)
  2)  Estado end-to-end de la cedula
  3)  Cedulas atascadas (no descartadas, finsian=false)
  4)  Cedulas huerfanas (uidgestor en ceros)
  5)  Logs de la cedula (sianlogsvarios + logsenviocedulapolicia)
  6)  Historial MP (notpolhistoricomp)
  7)  Estado origen ED (act + uje_act + dac + exp)
  8)  Stats por dia / dac_cod / perror
  9)  ACCIONES correctivas
  10) Consultar IURIX WEB (SQL-ACT-VIO-SIAN)
  q)  Salir
"""

MENU_ACCIONES = """
--- ACCIONES (cada una con preview + confirmacion) ---
  a)  A1  Descartar cedula (descartada=true, fechadescarte, usuario, motivo)
  b)  A2  Re-habilitar cedula descartada
  c)  A2b Forzar estado via INSERT en log (EXITO/ERROR/REENVIAR)
  d)  A4  Parche uidgestor / uidgestorcedula
  e)  A5  Insertar nota en sianlogsvarios
  f)  A3  Borrar cedula y hijas (solo si MP no la tomo)
  g)  A7  Marcar/des-marcar constancia (UPDATE ecedarchivosegnotid)
  h)  B1  (BD ED) Forzar es_enotif=0 para reprocesar
  back) Volver
"""


def submenu_acciones(conx: Conexiones):
    while True:
        print(MENU_ACCIONES)
        op = input(">>> accion: ").strip().lower()
        if op == "back" or op == "q":
            return
        elif op == "a": acc_descartar(conx)
        elif op == "b": acc_rehabilitar(conx)
        elif op == "c": acc_forzar_estado(conx)
        elif op == "d": acc_parche_uid(conx)
        elif op == "e": acc_nota_sianlog(conx)
        elif op == "f": acc_borrar_cedula(conx)
        elif op == "g": acc_marcar_constancia(conx)
        elif op == "h": acc_forzar_es_enotif_ed(conx)
        else: print(">>> opcion invalida")





class TablerosParCrudWindow:
    """CRUD amigable de tablerospar con grilla + formulario."""

    COLUMNS = [
        "par_id", "par_grupo", "par_clave", "par_subclave", "par_valor", "par_valor_json",
        "par_tipo", "par_ambiente", "par_activo", "par_descripcion", "par_usuario_alta",
        "par_usuario_modif",
    ]

    def __init__(self, parent, conx: Conexiones, write_log):
        self.parent = parent
        self.conx = conx
        self.conn = conx.panelws()
        self.write_log = write_log
        self.rows_by_id = {}

        self.win = tk.Toplevel(parent)
        self.win.title("CRUD TablerosPar")
        self.win.geometry("1250x650")

        filters = tk.LabelFrame(self.win, text="Filtros")
        filters.pack(fill=tk.X, padx=8, pady=6)
        self.var_grupo = tk.StringVar()
        self.var_clave = tk.StringVar()
        tk.Label(filters, text="Grupo").grid(row=0, column=0, padx=4, pady=4)
        tk.Entry(filters, textvariable=self.var_grupo, width=30).grid(row=0, column=1, padx=4, pady=4)
        tk.Label(filters, text="Clave contiene").grid(row=0, column=2, padx=4, pady=4)
        tk.Entry(filters, textvariable=self.var_clave, width=30).grid(row=0, column=3, padx=4, pady=4)
        tk.Button(filters, text="Filtrar", command=self.refresh).grid(row=0, column=4, padx=4, pady=4)
        tk.Button(filters, text="Limpiar", command=self.clear_filters).grid(row=0, column=5, padx=4, pady=4)
        tk.Button(filters, text="Agregar", command=self.open_create_form).grid(row=0, column=6, padx=4, pady=4)

        grid_fr = tk.Frame(self.win)
        grid_fr.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        show_cols = ["par_id", "par_grupo", "par_clave", "par_subclave", "par_tipo", "par_ambiente", "par_activo", "valor"]
        self.tree = ttk.Treeview(grid_fr, columns=show_cols, show="headings", height=18)
        for c in show_cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=130 if c != "valor" else 280, stretch=True)
        yscroll = ttk.Scrollbar(grid_fr, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._on_double_click)

        tk.Label(self.win, text="Doble click en una fila para editar / borrar.").pack(anchor="w", padx=10, pady=2)
        apply_neon_palette(self.win)
        self.refresh()

    def clear_filters(self):
        self.var_grupo.set("")
        self.var_clave.set("")
        self.refresh()

    def refresh(self):
        grupo = (self.var_grupo.get() or "").strip() or None
        clave = (self.var_clave.get() or "").strip() or None
        sql = """
            SELECT par_id, par_grupo, par_clave, par_subclave, par_valor, par_valor_json,
                   par_tipo, par_ambiente, par_activo, par_descripcion, par_usuario_alta,
                   par_usuario_modif
            FROM tablerospar
            WHERE (%(g)s IS NULL OR par_grupo = %(g)s)
              AND (%(c)s IS NULL OR par_clave ILIKE '%%' || %(c)s || '%%')
            ORDER BY par_grupo, par_clave, par_id
            LIMIT 500
        """
        rows = fetchall_dict(self.conn, sql, {"g": grupo, "c": clave})
        self.rows_by_id = {str(r["par_id"]): r for r in rows}
        for i in self.tree.get_children():
            self.tree.delete(i)
        for r in rows:
            valor = r.get("par_valor") if r.get("par_valor") is not None else str(r.get("par_valor_json") or "")
            self.tree.insert("", tk.END, iid=str(r["par_id"]), values=(
                r["par_id"], r.get("par_grupo"), r.get("par_clave"), r.get("par_subclave"),
                r.get("par_tipo"), r.get("par_ambiente"), r.get("par_activo"), valor
            ))
        self.write_log(f"CRUD tablerospar: {len(rows)} fila(s) cargadas.\n")

    def _on_double_click(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.rows_by_id.get(sel[0])
        if row:
            self.open_edit_form(row)

    def open_create_form(self):
        self._open_form("Agregar tablerospar")

    def open_edit_form(self, row):
        self._open_form(f"Editar tablerospar #{row['par_id']}", row=row)

    def _open_form(self, title, row=None):
        win = tk.Toplevel(self.win)
        win.title(title)
        win.geometry("700x560")
        vars_map = {}
        for idx, field in enumerate(self.COLUMNS):
            tk.Label(win, text=field).grid(row=idx, column=0, sticky="w", padx=8, pady=4)
            val = "" if row is None or row.get(field) is None else str(row.get(field))
            sv = tk.StringVar(value=val)
            vars_map[field] = sv
            tk.Entry(win, textvariable=sv, width=70).grid(row=idx, column=1, sticky="ew", padx=8, pady=4)

        vars_map["par_id"].set("" if row is None else str(row["par_id"]))

        def _normalize(v):
            return None if v is None or str(v).strip() == "" else str(v).strip()

        def do_save():
            values = {k: _normalize(v.get()) for k, v in vars_map.items()}
            values["par_activo"] = _parse_bool(values.get("par_activo"))
            if values["par_activo"] is None:
                messagebox.showerror("Error", "par_activo debe ser true/false")
                return
            values["par_tipo"] = (values.get("par_tipo") or "").upper()
            values["par_ambiente"] = (values.get("par_ambiente") or "TODOS").upper()
            if not values.get("par_grupo") or not values.get("par_clave"):
                messagebox.showerror("Error", "par_grupo y par_clave son requeridos")
                return
            if not messagebox.askyesno("Confirmar", "¿Confirmás guardar los cambios?", parent=win):
                return
            with self.conn.cursor() as cur:
                if row is None:
                    cur.execute("""
                        INSERT INTO tablerospar(par_grupo, par_clave, par_subclave, par_valor, par_valor_json, par_tipo,
                                                par_ambiente, par_activo, par_descripcion, par_usuario_alta)
                        VALUES (%(par_grupo)s, %(par_clave)s, %(par_subclave)s, %(par_valor)s, %(par_valor_json)s::jsonb,
                                %(par_tipo)s, %(par_ambiente)s, %(par_activo)s, %(par_descripcion)s, %(par_usuario_alta)s)
                    """, values)
                    self.write_log("tablerospar insertado.\n")
                else:
                    values["par_id"] = int(values["par_id"])
                    cur.execute("""
                        UPDATE tablerospar
                           SET par_grupo=%(par_grupo)s, par_clave=%(par_clave)s, par_subclave=%(par_subclave)s,
                               par_valor=%(par_valor)s, par_valor_json=%(par_valor_json)s::jsonb,
                               par_tipo=%(par_tipo)s, par_ambiente=%(par_ambiente)s, par_activo=%(par_activo)s,
                               par_descripcion=%(par_descripcion)s, par_usuario_modif=%(par_usuario_modif)s, par_fecha_modif=now()
                         WHERE par_id=%(par_id)s
                    """, values)
                    self.write_log(f"tablerospar #{values['par_id']} actualizado.\n")
            self.conn.commit()
            self.refresh()
            win.destroy()

        def do_delete():
            if row is None:
                return
            if not messagebox.askyesno("Confirmar", f"¿Seguro que querés borrar par_id={row['par_id']}?", parent=win):
                return
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM tablerospar WHERE par_id=%(id)s", {"id": row["par_id"]})
            self.conn.commit()
            self.write_log(f"tablerospar #{row['par_id']} borrado.\n")
            self.refresh()
            win.destroy()

        btnf = tk.Frame(win)
        btnf.grid(row=len(self.COLUMNS)+1, column=0, columnspan=2, pady=12)
        tk.Button(btnf, text="Guardar", command=do_save).pack(side=tk.LEFT, padx=6)
        if row is not None:
            tk.Button(btnf, text="Borrar", command=do_delete).pack(side=tk.LEFT, padx=6)
        tk.Button(btnf, text="Cancelar", command=win.destroy).pack(side=tk.LEFT, padx=6)




class GenericCrudWindow:
    def __init__(self, parent, conx: Conexiones, write_log, table: str, pk_fields, editable_fields):
        self.conn = conx.panelws()
        self.write_log = write_log
        self.table = table
        self.pk_fields = pk_fields
        self.editable_fields = editable_fields
        self.win = tk.Toplevel(parent)
        self.win.title(f"CRUD {table}")
        self.win.geometry("1100x620")

        top = tk.Frame(self.win)
        top.pack(fill=tk.X, padx=8, pady=6)
        tk.Button(top, text="Refrescar", command=self.refresh).pack(side=tk.LEFT, padx=4)
        tk.Button(top, text="Agregar", command=self.open_create_form).pack(side=tk.LEFT, padx=4)

        self.tree = ttk.Treeview(self.win, show="headings", height=20)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.tree.bind("<Double-1>", self._on_double_click)
        apply_neon_palette(self.win)
        self.refresh()

    def refresh(self):
        rows = fetchall_dict(self.conn, f"SELECT * FROM {self.table} ORDER BY 1 LIMIT 500")
        self.rows = rows
        cols = list(rows[0].keys()) if rows else (self.pk_fields + [f for f in self.editable_fields if f not in self.pk_fields])
        self.tree["columns"] = cols
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=160, stretch=True)
        for i in self.tree.get_children():
            self.tree.delete(i)
        for r in rows:
            key = "|".join(str(r.get(k)) for k in self.pk_fields)
            self.tree.insert("", tk.END, iid=key, values=[r.get(c) for c in cols])
        self.write_log(f"CRUD {self.table}: {len(rows)} fila(s).\n")

    def _on_double_click(self, _):
        sel = self.tree.selection()
        if not sel: return
        k = sel[0].split("|")
        row = None
        for r in self.rows:
            if [str(r.get(pk)) for pk in self.pk_fields] == k:
                row = r; break
        if row: self._open_form(f"Editar {self.table}", row)

    def open_create_form(self):
        self._open_form(f"Agregar {self.table}", None)

    def _open_form(self, title, row):
        w=tk.Toplevel(self.win); w.title(title); w.geometry("700x420")
        fields=list(dict.fromkeys(self.pk_fields + self.editable_fields))
        vars_map={}
        for i,f in enumerate(fields):
            tk.Label(w,text=f).grid(row=i,column=0,sticky="w",padx=6,pady=4)
            sv=tk.StringVar(value="" if row is None or row.get(f) is None else str(row.get(f)))
            vars_map[f]=sv
            tk.Entry(w,textvariable=sv,width=60).grid(row=i,column=1,padx=6,pady=4,sticky="ew")
        def save():
            vals={k:(v.get().strip() or None) for k,v in vars_map.items()}
            with self.conn.cursor() as cur:
                if row is None:
                    cols=", ".join(fields); ph=", ".join([f"%({f})s" for f in fields])
                    cur.execute(f"INSERT INTO {self.table} ({cols}) VALUES ({ph})", vals)
                else:
                    setf=[f for f in self.editable_fields if f not in self.pk_fields]
                    set_sql=", ".join([f"{f}=%({f})s" for f in setf])
                    where=" AND ".join([f"{k}=%({k})s" for k in self.pk_fields])
                    cur.execute(f"UPDATE {self.table} SET {set_sql} WHERE {where}", vals)
            self.conn.commit(); self.refresh(); w.destroy()
        def delete():
            if row is None: return
            vals={k:vars_map[k].get() for k in self.pk_fields}
            where=" AND ".join([f"{k}=%({k})s" for k in self.pk_fields])
            with self.conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.table} WHERE {where}", vals)
            self.conn.commit(); self.refresh(); w.destroy()
        bf=tk.Frame(w); bf.grid(row=len(fields)+1,column=0,columnspan=2,pady=10)
        tk.Button(bf,text="Guardar",command=save).pack(side=tk.LEFT,padx=5)
        if row is not None: tk.Button(bf,text="Borrar",command=delete).pack(side=tk.LEFT,padx=5)
        tk.Button(bf,text="Cancelar",command=w.destroy).pack(side=tk.LEFT,padx=5)
        apply_neon_palette(w)

class DiagCedulasGUI:
    """Interfaz grafica simple para ejecutar operaciones del menu."""

    def __init__(self, conx: Conexiones):
        self.conx = conx
        self.root = tk.Tk()
        configure_ttk_neon(self.root)
        self.root.title(f"Diagnostico Cedulas SIAN - {conx.sufijo}")
        self.root.geometry("1100x700")
        self.root.configure(bg=NEON_BG)

        top = tk.Frame(self.root)
        top.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(top, text=f"Ambiente: {conx.sufijo}", font=("Arial", 11, "bold")).pack(side=tk.LEFT)
        tk.Button(top, text="Limpiar salida", command=self._clear).pack(side=tk.RIGHT)

        btns = tk.Frame(self.root)
        btns.pack(fill=tk.X, padx=8, pady=6)

        acciones = [
            ("1 Buscar cedula", op_buscar_cedula, "Consulta una cedula puntual y muestra su trazabilidad entre tablas clave del flujo SIAN -> Policia."),
            ("2 Estado end-to-end", op_estado_end2end, "Muestra el estado integral de una cedula a lo largo del pipeline, incluyendo pasos intermedios y estado final."),
            ("3 Cedulas atascadas", op_atascadas, "Lista cedulas con procesamiento pendiente o bloqueado para facilitar diagnostico operativo."),
            ("4 Cedulas huerfanas", op_huerfanas, "Detecta registros incompletos o sin relacion esperada entre tablas del proceso."),
            ("5 Logs", op_logs, "Recupera eventos y mensajes historicos en logs para auditar errores o decisiones de negocio."),
            ("6 Historial MP", op_historico_mp, "Consulta el historial de movimientos/procesos para una cedula en el modulo MP."),
            ("7 Estado ED", op_estado_ed, "Verifica el estado actual en ED y ayuda a contrastar diferencias con DESTINO."),
            ("8 Stats", op_stats, "Genera metricas y conteos resumidos para monitorear volumen y salud del flujo."),
            ("10 IURIX WEB", op_consultar_iurix_web, "Lee SQL-ACT-VIO-SIAN desde DESTINO.parametro y ejecuta esa consulta en la BD IURIXWEB."),
            ("A1 Descartar", acc_descartar, "Marca una cedula como descartada de forma controlada tras confirmar la operacion."),
            ("A2 Re-habilitar", acc_rehabilitar, "Revierte un descarte para reingresar la cedula al circuito de procesamiento."),
            ("A2b Forzar estado", acc_forzar_estado, "Actualiza manualmente el estado de una cedula para destrabar casos excepcionales."),
            ("A4 Parche UID", acc_parche_uid, "Corrige o completa identificadores UID cuando hay inconsistencias de datos."),
            ("A5 Nota sianlog", acc_nota_sianlog, "Inserta una nota tecnica en sianlog para dejar trazabilidad de una intervencion."),
            ("A3 Borrar", acc_borrar_cedula, "Elimina registros de una cedula bajo confirmacion explicita y con vista previa previa."),
            ("A7 Constancia", acc_marcar_constancia, "Marca constancia de gestion para dejar evidencia administrativa en el sistema."),
            ("B1 Forzar ED", acc_forzar_es_enotif_ed, "Fuerza el indicador de e-notificacion en ED para sincronizar estados operativos."),
            ("CRUD tablerospar (con grilla)", self._open_tablerospar_crud, "Abre una grilla para crear, editar, filtrar y borrar parametros de tablerospar."),
            ("CRUD secrole", lambda c: self._open_generic_crud("secrole", ["secroleid"], ["secroleid", "secrolename", "secroledescription"]), "Administra roles de seguridad (alta, edicion y baja) sobre secrole."),
            ("CRUD secuser", lambda c: self._open_generic_crud("secuser", ["secuserid"], ["secuserid", "secusername", "secuserpassword"]), "Administra usuarios de seguridad y sus datos basicos en secuser."),
            ("CRUD juzgadosxrol", lambda c: self._open_generic_crud("juzgadosxrol", ["juzgadosxrolid"], ["juzgadosxrolid", "secroleid", "pdomicilioelectronicopj", "distrito_id"]), "Gestiona la asignacion de juzgados por rol y sus campos relacionados."),
            ("CRUD secuserrole", lambda c: self._open_generic_crud("secuserrole", ["secuserid", "secroleid"], ["secuserid", "secroleid"]), "Gestiona vinculaciones entre usuarios y roles en la tabla secuserrole."),
            ("Buscar secuser+rel", op_buscar_secuser_relaciones, "Busca un usuario y muestra sus relaciones de seguridad asociadas."),
        ]

        for i, (txt, fn, desc) in enumerate(acciones):
            tk.Button(btns, text=txt, width=22, command=lambda f=fn, t=txt, d=desc: self._run_action(t, f, d)).grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="ew")

        self.output = scrolledtext.ScrolledText(self.root, wrap=tk.WORD, font=("Consolas", 10), bg=NEON_BG, fg=NEON_FG, insertbackground=NEON_FG)
        self.output.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        apply_neon_palette(self.root)
        self._write("Interfaz iniciada. Ejecuta una accion con los botones superiores.\n")

    def _write(self, msg: str):
        self.output.insert(tk.END, msg)
        self.output.see(tk.END)
        self.root.update_idletasks()

    def _clear(self):
        self.output.delete("1.0", tk.END)

    def _ask(self, prompt: str) -> str:
        v = simpledialog.askstring("Entrada requerida", prompt, parent=self.root)
        return "" if v is None else v

    def _confirm(self, prompt: str) -> bool:
        return messagebox.askyesno("Confirmacion", prompt, parent=self.root)

    def _run_action(self, title: str, fn, description: str = ""):
        if description:
            self._write(f"Descripcion: {description}\n")
        self._write(f"\n===== {title} =====\n")
        old_input = builtins.input

        def gui_input(prompt=""):
            p = prompt.strip() or "Ingrese valor"
            if "confirma" in p.lower() or "confirmar" in p.lower() or "confirma delete" in p.lower():
                return "c" if self._confirm(p) else ""
            return self._ask(p)

        buf = io.StringIO()
        try:
            builtins.input = gui_input
            with redirect_stdout(buf):
                fn(self.conx)
        except Exception:
            buf.write("\nERROR inesperado:\n")
            buf.write(traceback.format_exc())
        finally:
            builtins.input = old_input
        self._write(buf.getvalue())

    def _open_tablerospar_crud(self, _conx):
        TablerosParCrudWindow(self.root, self.conx, self._write)

    def _open_generic_crud(self, table, pk_fields, editable_fields):
        GenericCrudWindow(self.root, self.conx, self._write, table, pk_fields, editable_fields)

    def run(self):
        self.root.mainloop()



def main():
    parser = argparse.ArgumentParser(description="Diagnostico cedulas SIAN")
    parser.add_argument("--test", type=int, default=0, help="1=TEST (.251), 0=PROD (.250)")
    parser.add_argument("--gui", action="store_true", help="Abrir interfaz grafica Tkinter")
    args = parser.parse_args()

    conx = Conexiones(test=bool(args.test))

    if args.gui:
        app = DiagCedulasGUI(conx)
        app.run()
        return

    try:
        while True:
            print(MENU_PRINCIPAL.format(sufijo=conx.sufijo))
            op = input(">>> opcion: ").strip().lower()
            try:
                if op == "q": break
                elif op == "1": op_buscar_cedula(conx)
                elif op == "2": op_estado_end2end(conx)
                elif op == "3": op_atascadas(conx)
                elif op == "4": op_huerfanas(conx)
                elif op == "5": op_logs(conx)
                elif op == "6": op_historico_mp(conx)
                elif op == "7": op_estado_ed(conx)
                elif op == "8": op_stats(conx)
                elif op == "9": submenu_acciones(conx)
                elif op == "10": op_consultar_iurix_web(conx)
                else: print(">>> opcion invalida")
            except KeyboardInterrupt:
                print("\n>>> volviendo al menu...")
            except Exception as e:
                # Si fallo a mitad de una operacion, asegurar que la conn no quede en aborted state
                try:
                    conx.destino().rollback()
                except Exception:
                    pass
                print(f"ERROR en la operacion: {e}")
    finally:
        conx.cerrar()
        print("Bye.")


if __name__ == "__main__":
    main()
