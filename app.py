import sqlite3, random
from datetime import datetime
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
        submitted = st.form_submit_button('Iniciar sesión', type='primary', use_container_width=True)
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

def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def db():
    c=sqlite3.connect(DB, check_same_thread=False); c.row_factory=sqlite3.Row; return c
def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS lots(lot_id TEXT PRIMARY KEY, product TEXT NOT NULL, qty INTEGER NOT NULL, created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'En producción', inspection TEXT NOT NULL DEFAULT 'Pendiente', notes TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, machine TEXT NOT NULL, lot_id TEXT, vibration REAL, temperature REAL, current_a REAL, state TEXT NOT NULL, message TEXT NOT NULL, simulated INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, lot_id TEXT, reading_id INTEGER, severity TEXT NOT NULL, description TEXT NOT NULL, action TEXT NOT NULL, responsible TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Abierto');
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL, details TEXT NOT NULL);
        ''')
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
    with db() as c:
        cur=c.execute('INSERT INTO readings(timestamp,machine,lot_id,vibration,temperature,current_a,state,message) VALUES(?,?,?,?,?,?,?,?)',(now(),machine,lotid or None,v,t,a,state,msg)); rid=cur.lastrowid
        if state in ('Advertencia','Crítico'):
            c.execute('INSERT INTO events(timestamp,lot_id,reading_id,severity,description,action,responsible) VALUES(?,?,?,?,?,?,?)',(now(),lotid or None,rid,'Alta' if state=='Crítico' else 'Media',msg,'Revisar equipo, validar señal y documentar acción','Mantenimiento (asignar)'))
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


# Recuperación temporal de la cuenta administradora.
# Eliminar este bloque después de recuperar el acceso.
RECOVERY_UID = "3ca3307d-2e01-419c-bdf8-6937e5b582b3"

recovery_code = st.secrets.get("RECOVERY_CODE", "")

if recovery_code:
    with st.expander("Recuperación temporal de administrador"):
        with st.form("temporary_admin_recovery"):
            entered_code = st.text_input(
                "Código temporal",
                type="password"
            )
            new_password = st.text_input(
                "Nueva contraseña",
                type="password"
            )
            confirm_password = st.text_input(
                "Confirmar contraseña",
                type="password"
            )
            submitted = st.form_submit_button(
                "Restablecer contraseña"
            )

        if submitted:
            if entered_code != recovery_code:
                st.error("Código temporal incorrecto.")
            elif len(new_password) < 12:
                st.error("Usa una contraseña de al menos 12 caracteres.")
            elif new_password != confirm_password:
                st.error("Las contraseñas no coinciden.")
            else:
                try:
                    sb_admin.auth.admin.update_user_by_id(
                        RECOVERY_UID,
                        {"password": new_password}
                    )
                    st.success(
                        "Contraseña actualizada. "
                        "Ya puedes iniciar sesión con la cuenta nueva."
                    )
                except Exception:
                    st.error(
                        "No se pudo cambiar la contraseña. "
                        "Revisa la configuración de Supabase."
                    )

init()

if handle_password_recovery():
    st.stop()

require_login()

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
t1,t2,t3,t4=st.tabs(['📊 Monitoreo','🏷️ Lotes y QR','🧾 Eventos e historial','🛡️ Validación y auditoría'])

with t1:
    st.header('Estado de la troqueladora')
    lots=df('SELECT lot_id FROM lots ORDER BY created DESC')['lot_id'].tolist(); lotopt=['(Sin asociar)']+lots
    selected=st.selectbox('Lote procesado, si se conoce',lotopt); selected=None if selected=='(Sin asociar)' else selected
    a,b,c=st.columns(3)
    v=a.number_input('Vibración (unidad simulada)',-1.0,20.0,3.2,0.1)
    t=b.number_input('Temperatura (°C)',-30.0,150.0,62.0,1.0)
    amps=c.number_input('Corriente (A)',-1.0,40.0,11.0,0.5)
    x,y=st.columns(2)
    if x.button('Registrar lectura',type='primary',use_container_width=True):
        state,msg=classify(v,t,amps)
        if state=='Inválida': st.error(msg); log('Rechazar lectura inválida',machine,{'vibration':v,'temperature':t,'current_a':amps})
        else:
            rid=add_reading(machine,selected,v,t,amps,state,msg)
            if state=='Normal': st.success(f'Lectura #{rid}: {state}. {msg}')
            elif state=='Advertencia': st.warning(f'Lectura #{rid}: {state}. {msg}')
            else: st.error(f'Lectura #{rid}: {state}. {msg}')
    if y.button('Generar lectura simulada',use_container_width=True):
        mode=random.choices(['normal','warning','critical'],[6,3,1])[0]
        vals=(round(random.uniform(2,4.8),1),round(random.uniform(45,73),1),round(random.uniform(7,14.5),1)) if mode=='normal' else ((round(random.uniform(5,6.9),1),round(random.uniform(75,84),1),round(random.uniform(15,17.8),1)) if mode=='warning' else (round(random.uniform(7,9),1),round(random.uniform(85,98),1),round(random.uniform(18,23),1)))
        state,msg=classify(*vals); rid=add_reading(machine,selected,*vals,state,msg); st.session_state['last']=f'Lectura #{rid}: {state} · {vals}'; st.rerun()
    if 'last' in st.session_state: st.caption(st.session_state['last'])
    readings=df('SELECT * FROM readings ORDER BY id DESC LIMIT 100')
    if readings.empty: st.info('Sin lecturas todavía. Registra o genera una lectura.')
    else:
        latest=readings.iloc[0]; m1,m2,m3,m4=st.columns(4); m1.metric('Vibración',f"{latest.vibration:.1f}"); m2.metric('Temperatura',f'{latest.temperature:.1f} °C'); m3.metric('Corriente',f'{latest.current_a:.1f} A'); m4.metric('Estado',latest.state)
        chart=readings.sort_values('id'); st.plotly_chart(px.line(chart,x='timestamp',y=['vibration','temperature','current_a'],markers=True,title='Lecturas registradas (series con unidades diferentes)'),use_container_width=True)
        st.dataframe(readings[['id','timestamp','machine','lot_id','vibration','temperature','current_a','state','message']],hide_index=True,use_container_width=True)

with t2:
    st.header('Crear lote')
    with st.form('newlot',clear_on_submit=True):
        l1,l2=st.columns(2); lid=l1.text_input('Identificador único',placeholder='AP-001'); product=l2.text_input('Producto/formato',value='Caja corrugada')
        qty=l1.number_input('Cantidad planificada (cajas)',1,1000000,5000,100); inspection=l2.selectbox('Estado de inspección',['Pendiente','Aprobado','Rechazado']); notes=st.text_area('Notas opcionales')
        submit=st.form_submit_button('Crear lote',type='primary')
        if submit:
            lid=lid.strip().upper()
            if not lid or qty<=0: st.error('Identificador obligatorio y cantidad mayor que cero.')
            else:
                try:
                    with db() as con: con.execute('INSERT INTO lots(lot_id,product,qty,created,status,inspection,notes) VALUES(?,?,?,?,?,?,?)',(lid,product.strip(),int(qty),now(),'En producción',inspection,notes.strip()))
                    log('Crear lote',lid,{'product':product,'qty':int(qty)}); st.success(f'Lote {lid} creado.')
                except sqlite3.IntegrityError: st.error('Ese identificador ya existe. Usa otro.')
    st.header('Consultar lote y QR')
    qtext=st.text_input('Código de lote o texto leído desde el QR',placeholder='AP-001 o AUSTRALPACK|LOTE|AP-001')
    qid=qtext.strip().upper()
    if qid.startswith('AUSTRALPACK|LOTE|'): qid=qid.split('|')[-1]
    if qtext:
        item=lot(qid)
        if not item: st.error('Lote inexistente. Verifica el código.'); log('Consulta lote inexistente',qid,{'input':qtext})
        else:
            left,right=st.columns([1,2]); image,payload=qr_png(qid); left.image(image,caption=f'QR del lote {qid}',width=220); left.download_button('Descargar QR PNG',image,f'QR_{qid}.png','image/png'); left.caption(f'Contenido: {payload}')
            right.success(f'Lote encontrado: {item["lot_id"]}'); right.write(f'**Producto:** {item["product"]}'); right.write(f'**Cantidad:** {item["qty"]:,} cajas'); right.write(f'**Creado:** {item["created"]}'); right.write(f'**Estado:** {item["status"]}'); right.write(f'**Inspección:** {item["inspection"]}'); right.write(f'**Notas:** {item["notes"] or "Sin notas"}')
            rd=df('SELECT * FROM readings WHERE lot_id=? ORDER BY id DESC',(qid,)); ev=df('SELECT * FROM events WHERE lot_id=? ORDER BY id DESC',(qid,)); right.metric('Lecturas asociadas',len(rd)); right.metric('Eventos asociados',len(ev))
            if not rd.empty: st.write('Lecturas del lote'); st.dataframe(rd[['timestamp','vibration','temperature','current_a','state']],hide_index=True,use_container_width=True)
            if not ev.empty: st.write('Eventos del lote'); st.dataframe(ev[['timestamp','severity','description','action','responsible','status']],hide_index=True,use_container_width=True)
    all_lots=df('SELECT * FROM lots ORDER BY created DESC'); st.subheader('Lotes registrados'); st.dataframe(all_lots,hide_index=True,use_container_width=True) if not all_lots.empty else st.info('Todavía no hay lotes.')

with t3:
    st.header('Eventos y seguimiento')
    events=df('SELECT * FROM events ORDER BY id DESC LIMIT 250')
    if events.empty: st.info('No hay eventos. Registra una lectura de advertencia o crítica.')
    else:
        st.dataframe(events,hide_index=True,use_container_width=True); eid=st.selectbox('Evento para actualizar',events.id.tolist()); row=events[events.id==eid].iloc[0]
        with st.form('updateevent'):
            status=st.selectbox('Estado',['Abierto','En revisión','Cerrado'],index=['Abierto','En revisión','Cerrado'].index(row.status)); resp=st.text_input('Responsable',row.responsible); action=st.text_area('Acción/seguimiento',row.action); save=st.form_submit_button('Guardar seguimiento')
            if save:
                with db() as con: con.execute('UPDATE events SET status=?,responsible=?,action=? WHERE id=?',(status,resp.strip() or 'Sin asignar',action.strip(),int(eid)))
                log('Actualizar evento',str(eid),{'status':status,'responsible':resp,'action':action}); st.success('Seguimiento guardado.'); st.rerun()
    st.subheader('Exportar'); r=df('SELECT * FROM readings ORDER BY id'); e=df('SELECT * FROM events ORDER BY id'); a,b=st.columns(2); a.download_button('Lecturas CSV',r.to_csv(index=False).encode('utf-8-sig'),'lecturas.csv','text/csv'); b.download_button('Eventos CSV',e.to_csv(index=False).encode('utf-8-sig'),'eventos.csv','text/csv')


with t4:
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

        with st.expander('Crear cuenta de trabajador o administrador'):
            with st.form('create_user_form', clear_on_submit=True):
                new_email = st.text_input('Usuario (correo electrónico)').strip().lower()
                new_name = st.text_input('Nombre completo')
                new_role = st.selectbox('Rol', ['worker', 'admin'], format_func=lambda x: 'Trabajador' if x == 'worker' else 'Administrador')
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

    if st.session_state.get('audit_error'):
        st.warning('Una acción no pudo registrarse en la auditoría centralizada. Contacta al administrador y revisa la conexión con Supabase.')
    st.warning('Limitaciones: lecturas simuladas; umbrales no validados; QR identifica el lote, pero el vínculo entre evento y lote debe confirmarse; no existe conexión con sensores físicos, PLC, SCADA ni ERP.')

st.divider(); st.caption('AustralPack · Prototipo académico A+C · Autenticación Supabase · Datos operacionales locales SQLite · Datos simulados · No usar para control industrial real.')
