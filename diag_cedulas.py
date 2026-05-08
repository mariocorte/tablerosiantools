"""Menu interactivo de diagnostico y acciones sobre cedulas SIAN -> Policia.

Reusa la configuracion de conexiones que ya esta cableada en core/config.py:
levanta el bootstrap (10.18.250.250 prod / .251 test), lee desde
public.parametro los JSONs de IMPTSIAN-PGSQL-IURIXPJ-{sufijo} (BD destino) y
IMPTSIAN-PGSQL-ED-{sufijo} (BD origen Expediente Digital), y abre conexiones
psycopg2 por demanda.

Uso:
    cd D:\\Proyectos\\Python\\notpol\\docker\\DockerimpTSian
    python tools/diag_cedulas.py            # PROD por default
    python tools/diag_cedulas.py --test 1   # TEST

Cada accion correctiva muestra un PREVIEW (SELECT) antes de aplicar el
INSERT/UPDATE/DELETE y pide confirmacion explicita escribiendo 'c'. Cualquier
otra cosa hace ROLLBACK.
"""
import argparse
import sys
import textwrap
from pathlib import Path

# Asegurar que el paquete core sea importable cuando se corre desde la raiz
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2
import psycopg2.extras

from core.config import cargar_parametro_json, get_bootstrap_config


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
    """Resuelve y cachea las conexiones psycopg2 a destino y a ED."""

    def __init__(self, test: bool):
        self.test = test
        self.sufijo = "TEST" if test else "PROD"
        self.bootstrap = get_bootstrap_config(test)
        print(f"[bootstrap] {self.bootstrap['host']}/{self.bootstrap['database']} ({self.sufijo})")
        self._cfg_destino = cargar_parametro_json(
            f"IMPTSIAN-PGSQL-IURIXPJ-{self.sufijo}", self.bootstrap
        )
        self._cfg_ed = cargar_parametro_json(
            f"IMPTSIAN-PGSQL-ED-{self.sufijo}", self.bootstrap
        )
        if not self._cfg_destino:
            sys.exit(f"ERROR: falta IMPTSIAN-PGSQL-IURIXPJ-{self.sufijo}")
        if not self._cfg_ed:
            print(f"AVISO: falta IMPTSIAN-PGSQL-ED-{self.sufijo} -- las consultas contra ED van a fallar.")
        self._conn_destino = None
        self._conn_ed = None

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

    def cerrar(self):
        for c in (self._conn_destino, self._conn_ed):
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


def main():
    parser = argparse.ArgumentParser(description="Diagnostico cedulas SIAN")
    parser.add_argument("--test", type=int, default=0, help="1=TEST (.251), 0=PROD (.250)")
    args = parser.parse_args()

    conx = Conexiones(test=bool(args.test))

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
