import sqlite3, random
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import pandas as pd
import plotly.express as px
import qrcode
import streamlit as st
from supabase import create_client

DB = Path(__file__).with_name('australpack.db')

st.set_page_config(page_title='AustralPack | A+C', page_icon='🏭', layout='wide')

# Supabase secrets are configured in Streamlit Community Cloud, never in this file.
try:
    SUPABASE_URL = st.secrets.get('SUPABASE_URL', '')
    SUPABASE_PUBLISHABLE_KEY = st.secrets.get('SUPABASE_PUBLISHABLE_KEY', '')
    SUPABASE_SECRET_KEY = st.secrets.get('SUPABASE_SECRET_KEY', '')
except Exception:
    SUPABASE_URL = SUPABASE_PUBLISHABLE_KEY = SUPABASE_SECRET_KEY = ''

if not all((SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY)):
    st.error('Falta configurar la conexión segura con Supabase.')
    st.markdown('En Streamlit Community Cloud, abre **Settings → Secrets** y configura `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` y `SUPABASE_SECRET_KEY`. No guardes estas claves en GitHub.')
    st.stop()

try:
    sb_admin = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
except Exception as exc:
    st.error(f'No se pudo inicializar Supabase: {exc}')
    st.stop()


def audit_cloud(action, entity, details):
    # Registra la acción en la auditoría persistente de Supabase.
    actor_id = st.session_state.get('user_id')
    try:
        sb_admin.table('audit_log').insert({
            'actor_id': actor_id,
            'action': str(action),
            'entity': str(entity),
            'entity_id': str(entity),
            'details': details if isinstance(details, dict) else {'value': str(details)}
        }).execute()
    except Exception as exc:
        st.session_state['audit_error'] = str(exc)


def show_login():
    st.title('AustralPack Circular SpA')
    st.subheader('Acceso de trabajadores')
    st.caption('Ingresa con la cuenta creada por el administrador.')

    with st.form('login_form'):
        email = st.text_input('Usuario (correo electrónico)').strip().lower()
        password = st.text_input('Contraseña', type='password')
        submitted = st.form_submit_button(
            'Iniciar sesión',
            type='primary',
            use_container_width=True
        )


    with st.form('forgot_password_form'):
        recovery_email = st.text_input(
            '¿Olvidaste tu contraseña? Ingresa tu correo electrónico',
            key='recovery_email'
        ).strip().lower()

        recovery_submitted = st.form_submit_button(
            'Solicitar restablecimiento de contraseña'
        )

    if recovery_submitted:
        if not recovery_email or '@' not in recovery_email:
            st.error('Ingresa un correo electrónico válido.')
        else:
            try:
                existing = (
                    sb_admin.table('user_profiles')
                    .select('username')
                    .eq('username', recovery_email)
                    .limit(1)
                    .execute()
                    .data or []
                )

                if not existing:
                    st.success(
                        'Si el correo corresponde a una cuenta de AustralPack, '
                        'la solicitud será revisada por el administrador.'
                    )
                else:
                    sb_admin.table('password_reset_requests').insert({
                        'email': recovery_email,
                        'status': 'pending'
                    }).execute()

                    st.success(
                        'Solicitud enviada. El administrador revisará tu '
                        'petición desde el panel de AustralPack.'
                    )

            except Exception as exc:
                st.error(
                    f'No se pudo registrar la solicitud. Inténtalo nuevamente. '
                    f'Detalle: {exc}'
                )


    if submitted:
        try:
            auth_client = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)
            result = auth_client.auth.sign_in_with_password({'email': email, 'password': password})
            user = result.user
            profile_result = sb_admin.table('user_profiles').select('*').eq('user_id', user.id).limit(1).execute()
            profile = profile_result.data[0] if profile_result.data else None
            if not profile:
                st.error('La cuenta todavía no tiene un perfil de AustralPack. Contacta al administrador.')
                return
            if not profile.get('active', False):
                st.error('Esta cuenta está desactivada. Contacta al administrador.')
                return
            st.session_state['user_id'] = user.id
            st.session_state['user_email'] = user.email or email
            st.session_state['user_role'] = profile['role']
            st.session_state['user_full_name'] = profile['full_name']
            audit_cloud('Inicio de sesión', 'auth', {'email': user.email})
            st.rerun()
        except Exception as exc:
            st.error(f'Error al iniciar sesión: {type(exc).__name__}: {exc}')


def require_login():
    uid = st.session_state.get('user_id')
    if not uid:
        show_login()
        st.stop()
    try:
        rows = sb_admin.table('user_profiles').select('*').eq('user_id', uid).limit(1).execute().data
        profile = rows[0] if rows else None
    except Exception:
        profile = None
    if not profile or not profile.get('active', False):
        for key in ('user_id', 'user_email', 'user_role', 'user_full_name'):
            st.session_state.pop(key, None)
        st.error('Tu sesión ya no está autorizada. Inicia sesión nuevamente o contacta al administrador.')
        show_login()
        st.stop()
    st.session_state['user_role'] = profile['role']
    st.session_state['user_full_name'] = profile['full_name']
    with st.sidebar:
        st.markdown('### Sesión')
        st.write(st.session_state['user_full_name'])
        st.caption(f"{st.session_state['user_email']} · {st.session_state['user_role']}")
        if st.button('Cerrar sesión', use_container_width=True):
            audit_cloud('Cierre de sesión', 'auth', {'email': st.session_state.get('user_email')})
            for key in ('user_id', 'user_email', 'user_role', 'user_full_name'):
                st.session_state.pop(key, None)
            st.rerun()

def change_own_password():
    with st.sidebar.expander("Cambiar mi contraseña"):
        with st.form("change_own_password_form"):
            current_password = st.text_input(
                "Contraseña actual",
                type="password"
            )
            new_password = st.text_input(
                "Nueva contraseña (mínimo 12 caracteres)",
                type="password"
            )
            confirm_password = st.text_input(
                "Confirmar nueva contraseña",
                type="password"
            )
            submitted = st.form_submit_button("Actualizar contraseña")

        if submitted:
            if len(new_password) < 12:
                st.error("La nueva contraseña debe tener al menos 12 caracteres.")
            elif new_password != confirm_password:
                st.error("Las contraseñas no coinciden.")
            else:
                try:
                    user_client = create_client(
                        SUPABASE_URL,
                        SUPABASE_PUBLISHABLE_KEY
                    )
                    user_client.auth.sign_in_with_password({
                        "email": st.session_state["user_email"],
                        "password": current_password
                    })
                    user_client.auth.update_user({
                        "password": new_password
                    })
                    st.success("Tu contraseña se actualizó correctamente.")
                except Exception:
                    st.error(
                        "No se pudo cambiar la contraseña. "
                        "Verifica tu contraseña actual e inténtalo de nuevo."
                    )

def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def shift_info(timestamp=None):
    """Devuelve el turno y su fecha operacional usando la zona local del servidor."""
    value = datetime.fromisoformat(timestamp) if timestamp else datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.astimezone()
    if value.hour >= 8 and value.hour < 20:
        return 'Día', value.date().isoformat()
    operational_day = value.date() if value.hour >= 20 else (value.date() - timedelta(days=1))
    return 'Noche', operational_day.isoformat()

def current_shift_fields():
    return shift_info(now())

def shift_filter(frame, key):
    """Agrega filtro consistente por turno sin cambiar las reglas de acceso."""
    if frame.empty or 'shift' not in frame.columns:
        return frame
    options = ['Todos'] + [s for s in ('Día', 'Noche') if s in frame['shift'].dropna().unique()]
    selected = st.selectbox('Filtrar por turno', options, key=key)
    return frame if selected == 'Todos' else frame[frame['shift'] == selected]

def db():
    c=sqlite3.connect(DB, check_same_thread=False); c.row_factory=sqlite3.Row; return c
def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS lots(lot_id TEXT PRIMARY KEY, product TEXT NOT NULL, qty INTEGER NOT NULL, created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'En producción', inspection TEXT NOT NULL DEFAULT 'Pendiente', notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, machine TEXT NOT NULL, lot_id TEXT, vibration REAL, temperature REAL, current_a REAL, state TEXT NOT NULL, message TEXT NOT NULL, simulated INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, lot_id TEXT, reading_id INTEGER, severity TEXT NOT NULL, description TEXT NOT NULL, action TEXT NOT NULL, responsible TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Abierto');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL, details TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS maintenance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    machine TEXT NOT NULL,
    lot_id TEXT,
    description TEXT NOT NULL,
    action TEXT NOT NULL,
    responsible TEXT NOT NULL,
    result TEXT NOT NULL,
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    check_type TEXT NOT NULL,
    result TEXT NOT NULL,
    observations TEXT,
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS financial_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    operational_date TEXT,
    shift TEXT,
    record_type TEXT NOT NULL CHECK(record_type IN ('Costo', 'Beneficio')),
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount >= 0),
    description TEXT NOT NULL,
    source TEXT,
    created_by TEXT
);
        ''')
        
        # Migración segura: identificar quién crea cada registro.
        for table in ("lots", "readings", "events"):
            columns = {
                row[1]
                for row in c.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "created_by" not in columns:
                c.execute(f"ALTER TABLE {table} ADD COLUMN created_by TEXT")

        # Migración aditiva e idempotente: conserva filas y permisos existentes.
        for table in ("lots", "readings", "events", "maintenance", "quality_checks"):
            columns = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
            for column in ("shift", "operational_date"):
                if column not in columns:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            date_column = "created" if table == "lots" else "timestamp"
            rows = c.execute(
                f"SELECT rowid, {date_column} FROM {table} WHERE shift IS NULL OR operational_date IS NULL"
            ).fetchall()
            for row in rows:
                try:
                    shift, operational_date = shift_info(row[1])
                except (TypeError, ValueError):
                    # Un timestamp legado inválido queda visible y se normaliza al día local.
                    shift, operational_date = 'Día', datetime.now().astimezone().date().isoformat()
                c.execute(
                    f"UPDATE {table} SET shift=?, operational_date=? WHERE rowid=?",
                    (shift, operational_date, row[0])
                )

        financial_columns = {row[1] for row in c.execute("PRAGMA table_info(financial_records)").fetchall()}
        for column in ("shift", "operational_date"):
            if column not in financial_columns:
                c.execute(f"ALTER TABLE financial_records ADD COLUMN {column} TEXT")
        for row in c.execute("SELECT rowid, timestamp FROM financial_records WHERE shift IS NULL OR operational_date IS NULL").fetchall():
            try:
                shift, operational_date = shift_info(row[1])
            except (TypeError, ValueError):
                shift, operational_date = 'Día', datetime.now().astimezone().date().isoformat()
            c.execute("UPDATE financial_records SET shift=?, operational_date=? WHERE rowid=?", (shift, operational_date, row[0]))

def log(action, entity, details):
    import json
    with db() as c:
        c.execute('INSERT INTO audit(timestamp,action,entity,details) VALUES(?,?,?,?)',
                  (now(), action, entity, json.dumps(details, ensure_ascii=False)))
    audit_cloud(action, entity, details)
def df(sql, params=()):
    with db() as c: return pd.read_sql_query(sql,c,params=params)
def lot(id):
    with db() as c: return c.execute('SELECT * FROM lots WHERE lot_id=?',(id,)).fetchone()
def classify(v,t,a):
    if v<0 or t < -20 or a<0: return 'Inválida','Lectura fuera de rango básico; no se registra como válida.'
    if v>=7 or t>=85 or a>=18: return 'Crítico','Umbral crítico ilustrativo superado: revisar equipo y aplicar protocolo seguro.'
    if v>=5 or t>=75 or a>=15: return 'Advertencia','Umbral ilustrativo superado: inspeccionar y registrar seguimiento.'
    return 'Normal','Lecturas bajo los umbrales ilustrativos.'
def add_reading(machine,lotid,v,t,a,state,msg):
    shift, operational_date = current_shift_fields()
    with db() as c:
        
        cur = c.execute(
            'INSERT INTO readings(timestamp,machine,lot_id,vibration,temperature,current_a,state,message,created_by,shift,operational_date) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (
                now(),
                machine,
                lotid or None,
                v,
                t,
                a,
                state,
                msg,
                st.session_state.get('user_id'), shift, operational_date
            )
        )
        rid = cur.lastrowid

        if state in ('Advertencia','Crítico'):
                c.execute(
                    'INSERT INTO events(timestamp,lot_id,reading_id,severity,description,action,responsible,created_by,shift,operational_date) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (
                        now(),
                        lotid or None,
                        rid,
                        'Alta' if state == 'Crítico' else 'Media',
                        msg,
                        'Revisar equipo, validar señal y documentar acción',
                        'Mantenimiento (asignar)',
                        st.session_state.get('user_id'), shift, operational_date
                    )
                )

    log('Registrar lectura simulada',machine,{'reading_id':rid,'lot_id':lotid,'vibration':v,'temperature':t,'current_a':a,'state':state})
    return rid
def qr_png(lotid):
    q=qrcode.QRCode(box_size=8,border=3); payload=f'AUSTRALPACK|LOTE|{lotid}'; q.add_data(payload); q.make(fit=True); im=q.make_image(fill_color='black',back_color='white'); b=BytesIO(); im.save(b,format='PNG'); return b.getvalue(),payload

def handle_password_recovery():
    token_hash = st.query_params.get("token_hash")
    recovery_type = st.query_params.get("type")

    if not token_hash or recovery_type != "recovery":
        return False

    if "recovery_client" not in st.session_state:
        try:
            recovery_client = create_client(
                SUPABASE_URL,
                SUPABASE_PUBLISHABLE_KEY
            )

            recovery_client.auth.verify_otp({
                "token_hash": token_hash,
                "type": "recovery"
            })

            st.session_state["recovery_client"] = recovery_client
            st.query_params.clear()

        except Exception:
            st.error(
                "El enlace de recuperación venció o no es válido. "
                "Solicita un correo nuevo."
            )
            return True

    st.title("AustralPack Circular SpA")
    st.header("Crear nueva contraseña")

    with st.form("password_recovery_form"):
        password1 = st.text_input(
            "Nueva contraseña",
            type="password"
        )
        password2 = st.text_input(
            "Repite la contraseña",
            type="password"
        )

        submitted = st.form_submit_button(
            "Guardar nueva contraseña",
            type="primary"
        )

    if submitted:
        if len(password1) < 10:
            st.error("La contraseña debe tener al menos 10 caracteres.")
        elif password1 != password2:
            st.error("Las contraseñas no coinciden.")
        else:
            try:
                recovery_client = st.session_state["recovery_client"]
                recovery_client.auth.update_user({
                    "password": password1
                })

                del st.session_state["recovery_client"]

                st.success(
                    "Contraseña actualizada. "
                    "Ya puedes iniciar sesión con tu nueva contraseña."
                )

            except Exception as exc:
                st.error(f"No se pudo actualizar la contraseña: {exc}")

    return True



init()

if handle_password_recovery():
    st.stop()

require_login()
change_own_password()

st.title('🏭 AustralPack Circular SpA')
st.subheader('Prototipo A + C · Monitoreo IoT simulado y trazabilidad por lote')
st.caption('Demostrador académico. Las lecturas y umbrales son simulados; no usar para operar maquinaria real.')
with st.sidebar:
    st.header('Equipo y reglas')
    machine=st.selectbox('Equipo',['TROQ-01 — Troqueladora'])
    st.write('Normal: vibración < 5; temperatura < 75 °C; corriente < 15 A.')
    st.write('Advertencia: vibración ≥ 5, temperatura ≥ 75 °C o corriente ≥ 15 A.')
    st.write('Crítico: vibración ≥ 7, temperatura ≥ 85 °C o corriente ≥ 18 A.')
    st.info('Los umbrales son ilustrativos y deben validarse con mantenimiento y el fabricante.')
t1,t2,t3,t4,t5,t6,t7=st.tabs([
    '📊 Monitoreo',
    '🏷️ Lotes y QR',
    '🧾 Eventos e historial',
    '🔧 Mantenimiento',
    '✅ Calidad',
    '🛡️ Validación y auditoría',
    '📈 Informes operacionales'
])
with t1:
    st.header('Estado de la troqueladora')
    lots=df('SELECT lot_id FROM lots ORDER BY created DESC')['lot_id'].tolist(); lotopt=['(Sin asociar)']+lots
    selected=st.selectbox('Lote procesado, si se conoce',lotopt); selected=None if selected=='(Sin asociar)' else selected
    a,b,c=st.columns(3)
    puede_registrar = st.session_state.get('user_role') in ('admin', 'operator')

    if puede_registrar:
        v = a.number_input('Vibración (unidad simulada)', -1.0, 20.0, 3.2, 0.1)
        t = b.number_input('Temperatura (°C)', -30.0, 150.0, 62.0, 1.0)
        amps = c.number_input('Corriente (A)', -1.0, 40.0, 11.0, 0.5)
    else:
        v, t, amps = 3.2, 62.0, 11.0
        st.info('Modo consulta: no tienes permisos para registrar lecturas.')
    
    x,y=st.columns(2)
    
    if puede_registrar and x.button(
        'Registrar lectura',
        type='primary',
        use_container_width=True
    ):
        state,msg=classify(v,t,amps)
        if state=='Inválida': 
            st.error(msg); 
            log('Rechazar lectura inválida', machine, {
                'vibration':v,
                'temperature':t,
                'current_a':amps
            })
        else:
            rid=add_reading(machine,selected,v,t,amps,state,msg)
            if state=='Normal': 
                st.success(f'Lectura #{rid}: {state}. {msg}')
            elif state=='Advertencia': 
                st.warning(f'Lectura #{rid}: {state}. {msg}')
            else: 
                st.error(f'Lectura #{-rid}: {state}. {msg}')
    
    if puede_registrar and y.button(
        'Generar lectura simulada',
        use_container_width=True
    ):
        mode=random.choices(['normal','warning','critical'],[6,3,1])[0]
        vals=(round(random.uniform(2,4.8),1),round(random.uniform(45,73),1),round(random.uniform(7,14.5),1)) if mode=='normal' else ((round(random.uniform(5,6.9),1),round(random.uniform(75,84),1),round(random.uniform(15,17.8),1)) if mode=='warning' else (round(random.uniform(7,9),1),round(random.uniform(85,98),1),round(random.uniform(18,23),1)))
        state,msg=classify(*vals); rid=add_reading(machine,selected,*vals,state,msg); st.session_state['last']=f'Lectura #{rid}: {state} · {vals}'; st.rerun()
    if 'last' in st.session_state: st.caption(st.session_state['last'])
    readings=df('SELECT * FROM readings ORDER BY id DESC LIMIT 100')
    if readings.empty: st.info('Sin lecturas todavía. Registra o genera una lectura.')
    else:
        latest=readings.iloc[0]; m1,m2,m3,m4=st.columns(4); m1.metric('Vibración',f"{latest.vibration:.1f}"); m2.metric('Temperatura',f'{latest.temperature:.1f} °C'); m3.metric('Corriente',f'{latest.current_a:.1f} A'); m4.metric('Estado',latest.state)
        chart=readings.sort_values('id'); st.plotly_chart(px.line(chart,x='timestamp',y=['vibration','temperature','current_a'],markers=True,title='Lecturas registradas (series con unidades diferentes)'),use_container_width=True)
        readings = shift_filter(readings, 'filter_readings')
        st.dataframe(
    readings[
        [
            'id', 'timestamp', 'shift', 'operational_date', 'machine', 'lot_id',
            'vibration', 'temperature', 'current_a',
            'state', 'message', 'created_by'
        ]
    ],
    hide_index=True,
    use_container_width=True
)

with t2:
    st.header('Crear lote')
    puede_crear_lote = st.session_state.get('user_role') in ('admin', 'operator')
    
    if puede_crear_lote:
        with st.form('newlot', clear_on_submit=True):
            l1,l2=st.columns(2); lid=l1.text_input('Identificador único',placeholder='AP-001'); product=l2.text_input('Producto/formato',value='Caja corrugada')
            qty=l1.number_input('Cantidad planificada (cajas)',1,1000000,5000,100); inspection=l2.selectbox('Estado de inspección',['Pendiente','Aprobado','Rechazado']); notes=st.text_area('Notas opcionales')
            submit=st.form_submit_button('Crear lote',type='primary')
            if submit:
                lid=lid.strip().upper()
                if not lid or qty<=0: st.error('Identificador obligatorio y cantidad mayor que cero.')
                else:
                    try:
                        with db() as con:
                            con.execute(
                                'INSERT INTO lots(lot_id,product,qty,created,status,inspection,notes,created_by,shift,operational_date) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                (
                                    lid,
                                    product.strip(),
                                    int(qty),
                                    now(),
                                    'En producción',
                                    inspection,
                                    notes.strip(),
                                    st.session_state.get('user_id'), *current_shift_fields()
                                )
                            )
                        log('Crear lote', lid, {'product': product, 'qty': int(qty)})
                        st.success(f'Lote {lid} creado.')
                    except sqlite3.IntegrityError:
                        st.error('Ese identificador ya existe. Usa otro.')
    st.header('Consultar lote y QR')
    qtext=st.text_input('Código de lote o texto leído desde el QR',placeholder='AP-001 o AUSTRALPACK|LOTE|AP-001')
    qid=qtext.strip().upper()
    if qid.startswith('AUSTRALPACK|LOTE|'): qid=qid.split('|')[-1]
    if qtext:
        item=lot(qid)
        if not item: st.error('Lote inexistente. Verifica el código.'); log('Consulta lote inexistente',qid,{'input':qtext})
        else:
            left,right=st.columns([1,2]); image,payload=qr_png(qid); left.image(image,caption=f'QR del lote {qid}',width=220); left.download_button('Descargar QR PNG',image,f'QR_{qid}.png','image/png'); left.caption(f'Contenido: {payload}')
            right.success(f'Lote encontrado: {item["lot_id"]}'); right.write(f'**Producto:** {item["product"]}'); right.write(f'**Cantidad:** {item["qty"]:,} cajas'); right.write(f'**Creado:** {item["created"]} · Turno {item["shift"]} · Fecha operacional {item["operational_date"]}'); right.write(f'**Estado:** {item["status"]}'); right.write(f'**Inspección:** {item["inspection"]}'); right.write(f'**Notas:** {item["notes"] or "Sin notas"}')
            rd=df('SELECT * FROM readings WHERE lot_id=? ORDER BY id DESC',(qid,)); ev=df('SELECT * FROM events WHERE lot_id=? ORDER BY id DESC',(qid,)); right.metric('Lecturas asociadas',len(rd)); right.metric('Eventos asociados',len(ev))
            if not rd.empty: st.write('Lecturas del lote'); st.dataframe(rd[['timestamp','shift','operational_date','vibration','temperature','current_a','state']],hide_index=True,use_container_width=True)
            if not ev.empty: st.write('Eventos del lote'); st.dataframe(ev[['timestamp','shift','operational_date','severity','description','action','responsible','status']],hide_index=True,use_container_width=True)
    all_lots = df('SELECT * FROM lots ORDER BY created DESC')
    st.subheader('Lotes registrados')
    all_lots = shift_filter(all_lots, 'filter_lots')
    st.dataframe(all_lots, hide_index=True, use_container_width=True)
    if all_lots.empty:
        st.info('Todavía no hay lotes.')
with t3:
    st.header('Eventos y seguimiento')
    events = df('SELECT * FROM events ORDER BY id DESC LIMIT 250')

    rol = st.session_state.get('user_role')

    if rol != 'admin':
        if 'created_by' in events.columns:
            events = events[
                events['created_by'] == st.session_state.get('user_id')
            ]
    events = shift_filter(events, 'filter_events')
    if events.empty: 
        st.info('No hay eventos. Registra una lectura de advertencia o crítica.')
    else:
        columnas_eventos = [
            'id', 'timestamp', 'shift', 'operational_date', 'lot_id', 'reading_id',
            'severity', 'description', 'action',
            'responsible', 'status', 'created_by'
        ]
        columnas_eventos = [
            col for col in columnas_eventos if col in events.columns
        ]
        
        st.dataframe(
            events[columnas_eventos],
            hide_index=True,
            use_container_width=True
        )
        

        if st.session_state.get('user_role') == 'admin':
            eid = st.selectbox(
                'Evento para actualizar',
                events.id.tolist()
            )
            row = events[events.id == eid].iloc[0]

            with st.form('updateevent'):
                status = st.selectbox(
                    'Estado',
                    ['Abierto', 'En revisión', 'Cerrado'],
                    index=['Abierto', 'En revisión', 'Cerrado'].index(row.status)
                )
                resp = st.text_input('Responsable', row.responsible)
                action = st.text_area('Acción/seguimiento', row.action)
                save = st.form_submit_button('Guardar seguimiento')

            if save:
                with db() as con:
                    cur = con.execute(
                        'UPDATE events SET status=?, responsible=?, action=? WHERE id=?',
                        (
                            status,
                            resp.strip() or 'Sin asignar',
                            action.strip(),
                            int(eid)
                        )
                    )

                if cur.rowcount == 1:
                    log(
                        'Actualizar evento',
                        str(eid),
                        {
                            'status': status,
                            'responsible': resp.strip() or 'Sin asignar',
                            'action': action.strip()
                        }
                    )
                    st.success('Seguimiento guardado.')
                    st.rerun()
                else:
                    st.error('No se pudo actualizar el evento.')
        else:
            st.info(
                'Modo consulta: solo el administrador puede modificar '
                'el seguimiento de los eventos.'
            )

    st.subheader('Exportar')

    if st.session_state.get('user_role') == 'admin':
        r = df('SELECT * FROM readings ORDER BY id')
        e = df('SELECT * FROM events ORDER BY id')
    else:
        uid = st.session_state.get('user_id')
        r = df(
            'SELECT * FROM readings WHERE created_by=? ORDER BY id',
            (uid,)
        )
        e = df(
            'SELECT * FROM events WHERE created_by=? ORDER BY id',
            (uid,)
        )

    r = shift_filter(r, 'filter_readings_export')
    e = shift_filter(e, 'filter_events_export')

    a, b = st.columns(2)
    a.download_button(
        'Lecturas CSV',
        r.to_csv(index=False).encode('utf-8-sig'),
        'lecturas.csv',
        'text/csv'
    )
    b.download_button(
        'Eventos CSV',
        e.to_csv(index=False).encode('utf-8-sig'),
        'eventos.csv',
        'text/csv'
    )
with t4:
    st.header('🔧 Intervenciones de mantenimiento')

    rol = st.session_state.get('user_role')
    puede_mantener = rol in ('admin', 'maintenance')

    if puede_mantener:
        with st.form('form_mantenimiento'):
            maquina = st.text_input(
                'Equipo',
                value='TROQ-01 — Troqueladora'
            )
            lote = st.text_input('Lote asociado (opcional)')
            descripcion = st.text_area('Problema o trabajo realizado')
            accion = st.text_area('Acción de mantenimiento')
            responsable = st.text_input(
                'Responsable',
                value=st.session_state.get('user_full_name', '')
            )
            resultado = st.selectbox(
                'Resultado',
                ['Operativo', 'Operativo con observaciones', 'Fuera de servicio']
            )
            guardar = st.form_submit_button('Guardar intervención')

        if guardar:
            if not descripcion.strip() or not accion.strip() or not responsable.strip():
                st.error('Completa la descripción, la acción y el responsable.')
            else:
                with db() as con:
                    con.execute(
                        '''INSERT INTO maintenance
                        (timestamp, machine, lot_id, description, action,
                         responsible, result, created_by, shift, operational_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (
                            now(), maquina.strip(), lote.strip() or None,
                            descripcion.strip(), accion.strip(),
                            responsable.strip(), resultado,
                            st.session_state.get('user_id'), *current_shift_fields()
                        )
                    )
                log('Registrar mantenimiento', maquina, {
                    'lot_id': lote.strip() or None,
                    'result': resultado
                })
                st.success('Intervención registrada.')
                st.rerun()
    else:
        st.info('Solo Mantenimiento y Administrador pueden registrar intervenciones.')

    if rol == 'admin':
        registros = df('SELECT * FROM maintenance ORDER BY id DESC')
    else:
        registros = df(
            'SELECT * FROM maintenance WHERE created_by=? ORDER BY id DESC',
            (st.session_state.get('user_id'),)
        )

    st.subheader('Historial de mantenimiento')
    registros = shift_filter(registros, 'filter_maintenance')
    if not registros.empty:
        st.dataframe(registros, hide_index=True, use_container_width=True)
    else:
        st.info('Todavía no hay intervenciones registradas.')


with t5:
    st.header('✅ Controles de calidad')

    rol = st.session_state.get('user_role')
    puede_calidad = rol in ('admin', 'quality')

    if puede_calidad:
        lotes = df('SELECT lot_id FROM lots ORDER BY created DESC')['lot_id'].tolist()

        if lotes:
            with st.form('form_calidad'):
                lote_calidad = st.selectbox('Lote', lotes)
                tipo_control = st.selectbox(
                    'Tipo de control',
                    ['Inspección visual', 'Dimensiones', 'Resistencia',
                     'Revisión de embalaje', 'Otro']
                )
                resultado_calidad = st.selectbox(
                    'Resultado',
                    ['Aprobado', 'Rechazado', 'Requiere revisión']
                )
                observaciones = st.text_area('Observaciones')
                guardar_calidad = st.form_submit_button('Guardar control')

            if guardar_calidad:
                with db() as con:
                    con.execute(
                        '''INSERT INTO quality_checks
                        (timestamp, lot_id, check_type, result, observations, created_by,
                         shift, operational_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                        (
                            now(), lote_calidad, tipo_control, resultado_calidad,
                            observaciones.strip(),
                            st.session_state.get('user_id'), *current_shift_fields()
                        )
                    )
                log('Registrar control de calidad', lote_calidad, {
                    'tipo': tipo_control,
                    'resultado': resultado_calidad
                })
                st.success('Control de calidad registrado.')
                st.rerun()
        else:
            st.info('Primero debe existir al menos un lote.')

    else:
        st.info('Solo Calidad y Administrador pueden registrar controles.')

    if rol == 'admin':
        controles = df('SELECT * FROM quality_checks ORDER BY id DESC')
    else:
        controles = df(
            'SELECT * FROM quality_checks WHERE created_by=? ORDER BY id DESC',
            (st.session_state.get('user_id'),)
        )

    st.subheader('Historial de controles')
    controles = shift_filter(controles, 'filter_quality')
    if not controles.empty:
        st.dataframe(controles, hide_index=True, use_container_width=True)
    else:
        st.info('Todavía no hay controles registrados.')

with t6:
    st.header('Validación y auditoría')
    st.table(pd.DataFrame([
        ['Normal','3.2 / 62 °C / 11 A','Debe registrar estado Normal'],
        ['Advertencia','5.5 / 70 °C / 12 A','Debe generar evento medio'],
        ['Crítica','7.5 / 90 °C / 19 A','Debe generar evento alto'],
        ['Dato inválido','-1 / 62 °C / 11 A','Debe rechazar lectura'],
        ['Lote inexistente','AP-99999','Debe avisar y auditar consulta'],
        ['Identificador duplicado','Crear dos veces AP-001','Debe impedir duplicado'],
    ], columns=['Prueba','Entrada (vibración / temperatura / corriente)','Resultado esperado']))

    if st.session_state.get('user_role') == 'admin':
        st.subheader('Auditoría centralizada')
        try:
            cloud_rows = sb_admin.table('audit_log').select('*').order('created_at', desc=True).limit(300).execute().data
            cloud_audit = pd.DataFrame(cloud_rows or [])
            if not cloud_audit.empty:
                profiles_for_audit = sb_admin.table('user_profiles').select('user_id,username,full_name').execute().data or []
                profiles_audit_df = pd.DataFrame(profiles_for_audit)
                if not profiles_audit_df.empty:
                    cloud_audit = cloud_audit.merge(profiles_audit_df, how='left', left_on='actor_id', right_on='user_id')
                st.dataframe(cloud_audit, hide_index=True, use_container_width=True)
                st.download_button('Descargar auditoría centralizada CSV',
                                   cloud_audit.to_csv(index=False).encode('utf-8-sig'),
                                   'auditoria_australpack.csv', 'text/csv')
            else:
                st.info('Todavía no hay acciones en la auditoría centralizada.')
        except Exception as exc:
            st.error(f'No se pudo consultar la auditoría centralizada: {exc}')


        st.subheader('Solicitudes de recuperación de contraseña')

        try:
            reset_requests = (
                sb_admin.table('password_reset_requests')
                .select('*')
                .eq('status', 'pending')
                .order('created_at')
                .execute()
                .data or []
            )

            if not reset_requests:
                st.success('No hay solicitudes de recuperación pendientes.')
            else:
                st.warning(
                    f'Hay {len(reset_requests)} solicitud(es) pendiente(s).'
                )

                for request in reset_requests:
                    with st.container(border=True):
                        st.write(f"**Correo:** {request['email']}")
                        st.caption(f"Solicitud recibida: {request['created_at']}")

                        col1, col2 = st.columns(2)

                        with col1:
                            if st.button(
                                'Restablecer contraseña',
                                key=f"reset_request_{request['id']}"
                            ):
                                st.session_state['reset_request_email'] = (
                                    request['email']
                                )
                                st.info(
                                    'Usa el formulario «Restablecer contraseña '
                                    'de un usuario» que aparece más abajo. '
                                    'Selecciona esta cuenta y establece una '
                                    'contraseña temporal.'
                                )

                        with col2:
                            if st.button(
                                'Marcar como resuelta',
                                key=f"resolve_request_{request['id']}"
                            ):
                                st.session_state[
                                    f"confirm_resolve_{request['id']}"
                                ] = True

                        if st.session_state.get(
                            f"confirm_resolve_{request['id']}", False
                        ):
                            st.warning(
                                'Confirma solo si ya resolviste la solicitud '
                                'o verificaste que no requiere acción.'
                            )

                            if st.button(
                                'Confirmar cierre',
                                key=f"confirm_close_{request['id']}"
                            ):
                                (
                                    sb_admin.table('password_reset_requests')
                                    .update({
                                        'status': 'resolved',
                                        'resolved_at': now(),
                                        'resolved_by': st.session_state.get(
                                            'user_email', ''
                                        )
                                    })
                                    .eq('id', request['id'])
                                    .execute()
                                )

                                st.session_state.pop(
                                    f"confirm_resolve_{request['id']}", None
                                )
                                st.success('Solicitud marcada como resuelta.')
                                st.rerun()

        except Exception as exc:
            st.error(f'No se pudieron cargar las solicitudes: {exc}')

        st.divider()

        st.subheader('Administrar usuarios')
        try:
            profiles = sb_admin.table('user_profiles').select('*').order('created_at', desc=True).execute().data or []
            profiles_df = pd.DataFrame(profiles)
            if not profiles_df.empty:
                st.dataframe(profiles_df[['username','full_name','role','active','created_at']],
                             hide_index=True, use_container_width=True)
            else:
                st.info('No hay perfiles creados todavía.')
        except Exception as exc:
            st.error(f'No se pudieron cargar los usuarios: {exc}')

        with st.expander('Crear cuenta de trabajador, jefatura o administrador'):
            with st.form('create_user_form', clear_on_submit=True):
                new_email = st.text_input('Usuario (correo electrónico)').strip().lower()
                new_name = st.text_input('Nombre completo')
                role_labels = {
                    'admin': 'Administrador',
                    'operator': 'Operador',
                    'quality': 'Calidad',
                    'maintenance': 'Mantenimiento',
                    'production_manager': 'Jefatura de Producción',
                    'quality_manager': 'Jefatura de Calidad',
                    'maintenance_manager': 'Jefatura de Mantenimiento',
                    'finance': 'Finanzas',
                    'general_manager': 'Gerencia General'
                }

                new_role = st.selectbox(
                    'Rol',
                    list(role_labels.keys()),
                    format_func=lambda role: role_labels[role]
                ) 
                new_password = st.text_input('Contraseña inicial (mínimo 10 caracteres)', type='password')
                create_submitted = st.form_submit_button('Crear cuenta', type='primary')
            if create_submitted:
                if not new_email or '@' not in new_email or not new_name.strip() or len(new_password) < 10:
                    st.error('Ingresa un correo válido, nombre y contraseña de al menos 10 caracteres.')
                else:
                    created_user = None
                    try:
                        created = sb_admin.auth.admin.create_user({
                            'email': new_email,
                            'password': new_password,
                            'email_confirm': True,
                            'user_metadata': {'full_name': new_name.strip()}
                        })
                        created_user = created.user
                        sb_admin.table('user_profiles').insert({
                            'user_id': created_user.id,
                            'username': new_email,
                            'full_name': new_name.strip(),
                            'role': new_role,
                            'active': True
                        }).execute()
                        audit_cloud('Crear cuenta', 'user', {'email': new_email, 'role': new_role})
                        st.success(f'Cuenta creada para {new_email}. Entrega la contraseña inicial por un canal privado.')
                        st.rerun()
                    except Exception as exc:
                        if created_user:
                            try:
                                sb_admin.auth.admin.delete_user(created_user.id)
                            except Exception:
                                pass
                        st.error(f'No se pudo crear la cuenta. Verifica que el correo no exista y que la configuración sea correcta. Detalle: {exc}')

        with st.expander('Restablecer contraseña de un usuario'):
            profiles_for_password = [
                p for p in profiles
                if p.get('user_id')
            ]

            if profiles_for_password:
                password_labels = {
                    f"{p['username']} — {p.get('full_name', '')}": p
                    for p in profiles_for_password
                }

                selected_password_label = st.selectbox(
                    'Cuenta a la que cambiar la contraseña',
                    list(password_labels.keys()),
                    key='password_reset_user'
                )
                selected_password_user = password_labels[
                    selected_password_label
                ]

                with st.form('admin_password_reset_form'):
                    new_password = st.text_input(
                        'Nueva contraseña (mínimo 10 caracteres)',
                        type='password'
                    )
                    confirm_password = st.text_input(
                        'Confirmar nueva contraseña',
                        type='password'
                    )
                    confirm_reset = st.checkbox(
                        'Confirmo que quiero cambiar la contraseña de esta cuenta'
                    )
                    reset_submitted = st.form_submit_button(
                        'Actualizar contraseña',
                        type='primary'
                    )

                if reset_submitted:
                    if len(new_password) < 10:
                        st.error('La contraseña debe tener al menos 10 caracteres.')
                    elif new_password != confirm_password:
                        st.error('Las contraseñas no coinciden.')
                    elif not confirm_reset:
                        st.error('Confirma la operación antes de continuar.')
                    else:
                        try:
                            sb_admin.auth.admin.update_user_by_id(
                                selected_password_user['user_id'],
                                {'password': new_password}
                            )
                            audit_cloud(
                                'Restablecer contraseña',
                                'user',
                                {'username': selected_password_user['username']}
                            )
                            st.success('Contraseña actualizada correctamente.')
                        except Exception as exc:
                            st.error(f'No se pudo actualizar la contraseña: {exc}')
            else:
                st.info('No hay usuarios disponibles.')

        with st.expander('Activar o desactivar una cuenta'):
            selectable = [p for p in profiles if p.get('user_id') != st.session_state.get('user_id')]
            if not selectable:
                st.info('No hay otras cuentas para administrar.')
            else:
                labels = {f"{p['username']} — {'Activa' if p['active'] else 'Desactivada'}": p for p in selectable}
                chosen_label = st.selectbox('Cuenta', list(labels.keys()))
                chosen = labels[chosen_label]
                desired_active = st.radio('Estado nuevo', [True, False],
                                          index=0 if chosen['active'] else 1,
                                          format_func=lambda x: 'Activa' if x else 'Desactivada',
                                          horizontal=True)
                if st.button('Guardar estado de cuenta', type='primary'):
                    try:
                        sb_admin.table('user_profiles').update({'active': desired_active}).eq('user_id', chosen['user_id']).execute()
                        sb_admin.auth.admin.update_user_by_id(
                            chosen['user_id'],
                            {'ban_duration': 'none' if desired_active else '876000h'}
                        )
                        audit_cloud('Cambiar estado de cuenta', 'user', {
                            'username': chosen['username'], 'active': desired_active
                        })
                        st.success('Estado de la cuenta actualizado.')
                        st.rerun()
                    except Exception as exc:
                        st.error(f'No se pudo actualizar la cuenta: {exc}')
    else:
        st.info('La auditoría centralizada y la administración de cuentas están disponibles solo para administradores.')

    local_audit = df('SELECT * FROM audit ORDER BY id DESC LIMIT 300') if st.session_state.get('user_role') == 'admin' else pd.DataFrame()
    if not local_audit.empty:
        st.subheader('Auditoría local del prototipo')
        st.dataframe(local_audit, hide_index=True, use_container_width=True)
        st.download_button('Descargar auditoría local CSV',
                           local_audit.to_csv(index=False).encode('utf-8-sig'),
                           'auditoria_local.csv', 'text/csv')

with t7:
    st.header('📈 Informes operacionales')
    st.caption('Consulta fallas, registros y acciones por área, período y turno. Cada fila conserva su fecha, responsable y referencia de origen.')

    rol_informe = st.session_state.get('user_role')
    uid_informe = st.session_state.get('user_id')
    if rol_informe in ('admin', 'finance'):
        with st.expander('Registrar costo o beneficio verificado'):
            st.caption('Ingresa montos reales respaldados por cotización, factura, presupuesto aprobado o medición. El sistema no estima ni inventa cifras.')
            with st.form('financial_record_form', clear_on_submit=True):
                tipo_fin = st.selectbox('Tipo', ['Costo', 'Beneficio'])
                categoria_fin = st.selectbox('Categoría', ['Equipamiento', 'Integración', 'Soporte', 'Detención', 'Capacitación', 'Beneficio operacional', 'Otro'])
                monto_fin = st.number_input('Monto (CLP)', min_value=0, step=10000, value=0)
                descripcion_fin = st.text_input('Descripción')
                respaldo_fin = st.text_input('Respaldo o referencia (cotización, factura, medición)')
                guardar_fin = st.form_submit_button('Guardar registro financiero')
            if guardar_fin:
                if not descripcion_fin.strip() or monto_fin <= 0:
                    st.error('Ingresa una descripción y un monto mayor que cero.')
                else:
                    fin_timestamp = now()
                    fin_shift, fin_date = shift_info(fin_timestamp)
                    with db() as con:
                        con.execute(
                            '''INSERT INTO financial_records
                            (timestamp, operational_date, shift, record_type, category,
                             amount, description, source, created_by)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                            (fin_timestamp, fin_date, fin_shift, tipo_fin, categoria_fin,
                             float(monto_fin), descripcion_fin.strip(), respaldo_fin.strip(), uid_informe)
                        )
                    log('Registrar dato financiero', categoria_fin, {
                        'tipo': tipo_fin, 'monto_clp': float(monto_fin),
                        'respaldo': respaldo_fin.strip()
                    })
                    st.success('Registro financiero guardado con su fecha, turno y responsable.')
                    st.rerun()
    scope_por_rol = {
        'production_manager': {'lots', 'readings', 'events'},
        'quality_manager': {'lots', 'readings', 'events', 'quality_checks'},
        'maintenance_manager': {'readings', 'events', 'maintenance'},
        'finance': {'financial_records'},
    }
    report_tables = ('lots', 'readings', 'events', 'maintenance', 'quality_checks', 'financial_records')
    if rol_informe in ('admin', 'general_manager'):
        report_scope = set(report_tables)
        own_records_only = False
    elif rol_informe in scope_por_rol:
        report_scope = scope_por_rol[rol_informe]
        own_records_only = False
    else:
        # Trabajadores mantienen su visibilidad actual: sus propios registros.
        report_scope = set(report_tables)
        own_records_only = True

    report_frames = {}
    for table in report_tables:
        if table not in report_scope:
            report_frames[table] = df(f'SELECT * FROM {table} WHERE 1=0')
        elif own_records_only and table != 'financial_records':
            report_frames[table] = df(f'SELECT * FROM {table} WHERE created_by=? ORDER BY id DESC', (uid_informe,))
        elif own_records_only:
            report_frames[table] = df(f'SELECT * FROM {table} WHERE created_by=? ORDER BY id DESC', (uid_informe,))
        else:
            order_col = 'created' if table == 'lots' else ('timestamp' if table == 'financial_records' else 'id')
            report_frames[table] = df(f'SELECT * FROM {table} ORDER BY {order_col} DESC')

    lotes_inf = report_frames['lots']
    lecturas_inf = report_frames['readings']
    eventos_inf = report_frames['events']
    mant_inf = report_frames['maintenance']
    calidad_inf = report_frames['quality_checks']
    finanzas_inf = report_frames['financial_records']

    hoy_local = datetime.now().astimezone().date()
    f1, f2, f3 = st.columns(3)
    periodo_inf = f2.date_input('Período operacional', value=(hoy_local - timedelta(days=6), hoy_local), key='report_dates')
    turno_inf = f3.selectbox('Turno', ['Todos', 'Día', 'Noche'], key='report_shift')

    if isinstance(periodo_inf, (tuple, list)) and len(periodo_inf) == 2:
        desde_inf, hasta_inf = periodo_inf
    else:
        desde_inf = hasta_inf = periodo_inf
    if desde_inf > hasta_inf:
        st.error('La fecha inicial debe ser anterior o igual a la fecha final.')
    else:
        dias_inf = (hasta_inf - desde_inf).days + 1
        prev_hasta = desde_inf - timedelta(days=1)
        prev_desde = prev_hasta - timedelta(days=dias_inf - 1)

        def recortar_reporte(frame, date_col):
            if frame.empty:
                return frame.copy()
            dates = pd.to_datetime(frame['operational_date'], errors='coerce').dt.date
            result = frame.loc[(dates >= desde_inf) & (dates <= hasta_inf)].copy()
            if turno_inf != 'Todos' and 'shift' in result.columns:
                result = result[result['shift'] == turno_inf]
            return result

        def recortar_anterior(frame):
            if frame.empty:
                return frame.copy()
            dates = pd.to_datetime(frame['operational_date'], errors='coerce').dt.date
            result = frame.loc[(dates >= prev_desde) & (dates <= prev_hasta)].copy()
            if turno_inf != 'Todos' and 'shift' in result.columns:
                result = result[result['shift'] == turno_inf]
            return result

        datasets = {
            'Producción': [('Lotes', lotes_inf, 'created'), ('Lecturas', lecturas_inf, 'timestamp')],
            'Eventos y fallas': [('Eventos', eventos_inf, 'timestamp')],
            'Mantenimiento': [('Intervenciones', mant_inf, 'timestamp')],
            'Calidad': [('Controles', calidad_inf, 'timestamp')],
            'Finanzas': [('Costos y beneficios', finanzas_inf, 'timestamp')]
        }
        areas_por_rol = {
            'admin': list(datasets),
            'general_manager': list(datasets),
            'operator': ['Producción', 'Eventos y fallas'],
            'maintenance': ['Eventos y fallas', 'Mantenimiento'],
            'quality': ['Calidad'],
            'production_manager': ['Producción', 'Eventos y fallas'],
            'quality_manager': ['Calidad'],
            'maintenance_manager': ['Eventos y fallas', 'Mantenimiento'],
            'finance': ['Finanzas']
        }
        areas_permitidas = areas_por_rol.get(rol_informe, [])
        area_options = ['Resumen general'] + areas_permitidas
        area_inf = f1.selectbox('Área', area_options, key='report_area')
        areas_a_mostrar = areas_permitidas if area_inf == 'Resumen general' else [area_inf]
        metric_rows = []
        current_data = {}
        for area in areas_a_mostrar:
            for label, frame, date_col in datasets[area]:
                actual = recortar_reporte(frame, date_col)
                anterior = recortar_anterior(frame)
                current_data[label] = actual
                metric_rows.append({'Área': area, 'Registros': label, 'Período actual': len(actual), 'Período anterior': len(anterior), 'Variación': len(actual) - len(anterior)})

        summary = pd.DataFrame(metric_rows)
        st.caption(f'Período comparado: {desde_inf} a {hasta_inf} · período anterior equivalente: {prev_desde} a {prev_hasta}')
        st.dataframe(summary, hide_index=True, use_container_width=True)

        if 'Finanzas' in areas_a_mostrar:
            fin_actual = recortar_reporte(finanzas_inf, 'timestamp')
            fin_anterior = recortar_anterior(finanzas_inf)
            costo_actual = float(fin_actual.loc[fin_actual['record_type'] == 'Costo', 'amount'].sum()) if not fin_actual.empty else 0.0
            beneficio_actual = float(fin_actual.loc[fin_actual['record_type'] == 'Beneficio', 'amount'].sum()) if not fin_actual.empty else 0.0
            costo_anterior = float(fin_anterior.loc[fin_anterior['record_type'] == 'Costo', 'amount'].sum()) if not fin_anterior.empty else 0.0
            beneficio_anterior = float(fin_anterior.loc[fin_anterior['record_type'] == 'Beneficio', 'amount'].sum()) if not fin_anterior.empty else 0.0
            st.subheader('Resumen financiero (CLP)')
            fm1, fm2, fm3 = st.columns(3)
            fm1.metric('Costos registrados', f'${costo_actual:,.0f}', delta=f'${costo_actual-costo_anterior:,.0f} vs. período anterior', delta_color='inverse')
            fm2.metric('Beneficios documentados', f'${beneficio_actual:,.0f}', delta=f'${beneficio_actual-beneficio_anterior:,.0f} vs. período anterior')
            fm3.metric('Beneficio neto documentado', f'${beneficio_actual-costo_actual:,.0f}')
            st.caption('El beneficio neto suma solo beneficios ingresados con respaldo y resta los costos registrados. No equivale a una proyección de retorno de inversión.')

        if rol_informe in ('admin', 'general_manager'):
            eventos_actuales = recortar_reporte(eventos_inf, 'timestamp')
            mant_actual = recortar_reporte(mant_inf, 'timestamp')
            calidad_actual = recortar_reporte(calidad_inf, 'timestamp')
            rechazados = int(calidad_actual['result'].eq('Rechazado').sum()) if not calidad_actual.empty and 'result' in calidad_actual.columns else 0
            pendientes_ej = int(eventos_actuales['status'].isin(['Abierto', 'En revisión']).sum()) if not eventos_actuales.empty and 'status' in eventos_actuales.columns else 0
            st.subheader('Resumen ejecutivo')
            em1, em2, em3, em4 = st.columns(4)
            em1.metric('Lecturas registradas', len(recortar_reporte(lecturas_inf, 'timestamp')))
            em2.metric('Eventos del período', len(eventos_actuales))
            em3.metric('Eventos pendientes', pendientes_ej)
            em4.metric('Controles rechazados', rechazados)
            st.caption(f'Intervenciones de mantenimiento: {len(mant_actual)}. Los indicadores describen registros de operación; no evalúan el desempeño de una persona o turno por sí solos.')

        if area_inf in ('Resumen general', 'Eventos y fallas'):
            ev_report = recortar_reporte(eventos_inf, 'timestamp')
            if not ev_report.empty:
                st.subheader('Fallas, recurrencia y seguimiento')
                st.caption('La aplicación etiqueta lecturas como inválidas si vibración o corriente son negativas o la temperatura es menor a −20 °C; genera advertencia desde vibración 5, temperatura 75 °C o corriente 15 A, y crítica desde 7, 85 °C o 18 A. Estos umbrales son ilustrativos y deben validarse con mantenimiento. El estado y mensaje de la lectura quedan vinculados al evento.')
                evcols = [c for c in ('severity', 'status') if c in ev_report.columns]
                if evcols:
                    estado_resumen = ev_report.groupby(evcols, dropna=False).size().reset_index(name='Cantidad')
                    estado_resumen = estado_resumen.rename(columns={'severity': 'Severidad', 'status': 'Estado de seguimiento'})
                    st.dataframe(estado_resumen, hide_index=True, use_container_width=True)
                group_key = 'lot_id' if 'lot_id' in ev_report.columns and ev_report['lot_id'].notna().any() else None
                if group_key:
                    repetidos = ev_report[ev_report[group_key].notna()].groupby(group_key).size().reset_index(name='Eventos')
                    repetidos = repetidos[repetidos['Eventos'] > 1].sort_values('Eventos', ascending=False)
                    if not repetidos.empty:
                        st.write('Lotes con eventos repetidos en el período')
                        st.dataframe(repetidos, hide_index=True, use_container_width=True)
                cerrados = int(ev_report['status'].eq('Cerrado').sum()) if 'status' in ev_report.columns else 0
                abiertos = int(ev_report['status'].isin(['Abierto', 'En revisión']).sum()) if 'status' in ev_report.columns else 0
                c1, c2, c3 = st.columns(3)
                c1.metric('Eventos del período', len(ev_report))
                c2.metric('Pendientes / en revisión', abiertos)
                c3.metric('Cerrados', cerrados)

        if area_inf in ('Resumen general', 'Calidad'):
            q_report = recortar_reporte(calidad_inf, 'timestamp')
            if not q_report.empty and 'result' in q_report.columns:
                st.subheader('Resultados de calidad')
                st.dataframe(q_report.groupby('result', dropna=False).size().reset_index(name='Controles'), hide_index=True, use_container_width=True)

        detalle_frames = []
        area_map = {'Lotes': 'Producción', 'Lecturas': 'Producción', 'Eventos': 'Eventos y fallas', 'Intervenciones': 'Mantenimiento', 'Controles': 'Calidad', 'Costos y beneficios': 'Finanzas'}
        allowed_labels = [label for area in areas_a_mostrar for label, _, _ in datasets[area]]
        for label in allowed_labels:
            frame = current_data.get(label, pd.DataFrame()).copy()
            if not frame.empty:
                frame.insert(0, 'report_area', area_map[label])
                frame.insert(1, 'record_type', label)
                detalle_frames.append(frame)
        st.subheader('Detalle trazable del período')
        if detalle_frames:
            detalle = pd.concat(detalle_frames, ignore_index=True, sort=False)
            if 'reading_id' in detalle.columns and not lecturas_inf.empty:
                lookup = lecturas_inf.drop_duplicates('id').set_index('id')
                for source_col, target_col in (('machine', 'machine'), ('state', 'state'), ('message', 'message')):
                    if source_col in lookup.columns:
                        values = detalle['reading_id'].map(lookup[source_col].to_dict())
                        if target_col in detalle.columns:
                            detalle[target_col] = detalle[target_col].where(detalle[target_col].notna(), values)
                        else:
                            detalle[target_col] = values
            if 'created_by' in detalle.columns:
                detalle['creator_id'] = detalle['created_by']
                try:
                    perfiles_nombres = sb_admin.table('user_profiles').select('user_id,full_name,username').execute().data or []
                    nombres_por_id = {
                        p['user_id']: f"{p.get('full_name') or p.get('username')} ({p.get('username')})"
                        for p in perfiles_nombres if p.get('user_id')
                    }
                    detalle['created_by'] = detalle['created_by'].map(nombres_por_id).fillna(detalle['creator_id'])
                except Exception:
                    pass
            labels_es = {
                'report_area': 'Área', 'record_type': 'Tipo de registro',
                'timestamp': 'Fecha y hora', 'created': 'Fecha de creación',
                'shift': 'Turno', 'operational_date': 'Fecha operacional',
                'machine': 'Equipo', 'lot_id': 'Lote', 'reading_id': 'Lectura asociada',
                'severity': 'Severidad', 'description': 'Descripción',
                'action': 'Acción', 'responsible': 'Responsable',
                'status': 'Estado de seguimiento', 'created_by': 'Registrado por',
                'result': 'Resultado', 'observations': 'Observaciones',
                'check_type': 'Tipo de control', 'product': 'Producto',
                'qty': 'Cantidad planificada', 'inspection': 'Inspección',
                'vibration': 'Vibración', 'temperature': 'Temperatura',
                'current_a': 'Corriente', 'state': 'Estado de lectura',
                'message': 'Validación / detalle de lectura',
                'source': 'Respaldo o referencia', 'record_type': 'Tipo de registro',
                'category': 'Categoría', 'amount': 'Monto (CLP)',
                'creator_id': 'ID de usuario'
            }
            detalle = detalle.rename(columns=labels_es)
            st.dataframe(detalle, hide_index=True, use_container_width=True)
            st.download_button('Descargar informe operacional CSV', detalle.to_csv(index=False).encode('utf-8-sig'), 'informe_operacional.csv', 'text/csv')
        else:
            st.info('No hay registros para los filtros seleccionados.')

    if st.session_state.get('audit_error'):
        st.warning('Una acción no pudo registrarse en la auditoría centralizada. Contacta al administrador y revisa la conexión con Supabase.')
    st.warning('Limitaciones: lecturas simuladas; umbrales no validados; QR identifica el lote, pero el vínculo entre evento y lote debe confirmarse; no existe conexión con sensores físicos, PLC, SCADA ni ERP.')

st.divider()
st.caption("AustralPack · Prototipo académico A+C · Autenticación Supabase · Datos operacionales locales SQLite · Datos simulados · No usar para control industrial real.")
