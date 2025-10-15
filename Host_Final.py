# app_transformador.py (versión con secciones reordenadas y labels aclaradas)
# - TXT/CSV diario del ESP (no se modifica)
# - kVA -> I_nom calculada asumiendo SIEMPRE 380 V trifásico
# - Temperatura ambiente (Open-Meteo con caché) u opción CSV (obligatoria)
# - Tabla del día
# - Guardar CSV combinado (debajo de la tabla)
# - Estimación térmica + vida útil (debajo del guardado), con parámetros en UNA FILA y labels explicativas
# - Gráficas: Corriente y FAA

import os, time, math, requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
from datetime import date, datetime

# ===================== Config general
st.set_page_config(page_title="Transformador · Vida térmica", layout="wide")
st.title("Monitor diario · Corriente + Temperatura + Vida útil")
st.caption("Lee un TXT/CSV diario, agrega temperatura ambiente y estima vida térmica (modelo IEEE/IEC simplificado).")

# ===================== Sidebar: archivo de datos (ESP)
st.sidebar.header("📁 Datos de entrada (ESP32)")
carpeta = st.sidebar.text_input("Carpeta de datos", value=r"C:\Users\NoteBook\Desktop\Demo\Txt_Datos")
patron = st.sidebar.text_input("Patrón de archivo diario", value="mediciones_%Y-%m-%d.txt")
fecha_sel = st.sidebar.date_input("Fecha a visualizar", value=date.today())

sep_opt = st.sidebar.selectbox("Separador", [",", ";", r"\t", "|"], index=0)
sep = "\t" if sep_opt == r"\t" else sep_opt
tiene_header = st.sidebar.checkbox("Primera fila trae encabezados", value=True)
col_time = st.sidebar.text_input("Nombre columna timestamp", value="timestamp")
col_I    = st.sidebar.text_input("Nombre columna corriente [A]", value="corriente")

auto = st.sidebar.toggle("Auto-actualizar", value=False)
intervalo = st.sidebar.slider("Intervalo de refresco (s)", 3, 60, 10, 1)
max_rows = st.sidebar.number_input("Máx. filas en memoria (0 = sin límite)", 0, 1_000_000, 0, 1000)

# ===================== Sidebar: transformador (SIEMPRE tri 380 V)
st.sidebar.header("⚙️ Transformador (trifásico 380 V)")
kva = st.sidebar.number_input("Potencia nominal [kVA]", value=25.0, min_value=0.1, step=0.5)
V_LL = 380.0  # fijo, 3F-380V
I_nom_auto = (kva * 1000.0) / (math.sqrt(3) * V_LL)
st.session_state["I_nom_auto"] = I_nom_auto

# ===================== Sidebar: temperatura ambiente
st.sidebar.header("🌡️ Temperatura ambiente (obligatoria)")
temp_source = st.sidebar.radio("Fuente de temperatura", ["Open-Meteo (automático)", "Subir CSV"], index=0)
lat = st.sidebar.number_input("Latitud", value=-31.40, step=0.01)
lon = st.sidebar.number_input("Longitud", value=-64.19, step=0.01)
up_temp = st.sidebar.file_uploader("CSV temp (timestamp,temp_c)", type=["csv"]) if temp_source == "Subir CSV" else None

# ===================== Helpers
def archivo_del_dia(base_dir: str, patron_fecha: str, dia: date) -> str:
    if not base_dir:
        return ""
    nombre = datetime(dia.year, dia.month, dia.day).strftime(patron_fecha)
    return os.path.join(base_dir, nombre)

ruta = archivo_del_dia(carpeta, patron, fecha_sel)
st.info(f"Archivo del día: `{ruta}`")

# Estado lectura incremental
if "file_key" not in st.session_state: st.session_state["file_key"] = None
if "file_pos" not in st.session_state: st.session_state["file_pos"] = 0
if "df" not in st.session_state: st.session_state["df"] = pd.DataFrame()
if "cols" not in st.session_state: st.session_state["cols"] = None

def leer_lineas_nuevas(path: str, sep: str, header: bool):
    if not os.path.exists(path):
        return pd.DataFrame(), "El archivo aún no existe."
    # reinicio por cambio de archivo
    if st.session_state["file_key"] != path:
        st.session_state["file_key"] = path
        st.session_state["file_pos"] = 0
        st.session_state["df"] = pd.DataFrame()
        st.session_state["cols"] = None
    size = os.path.getsize(path)
    if st.session_state["file_pos"] > size:
        st.session_state["file_pos"] = 0
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(st.session_state["file_pos"])
        chunk = f.read()
        st.session_state["file_pos"] = f.tell()
    if not chunk:
        return pd.DataFrame(), None
    lines = [ln for ln in chunk.splitlines() if ln.strip()]
    if header and st.session_state["cols"] is None and lines:
        st.session_state["cols"] = [c.strip() for c in lines[0].split(sep)]
        lines = lines[1:]
    rows = [[c.strip() for c in ln.split(sep)] for ln in lines]
    if st.session_state["cols"] is not None:
        k = len(st.session_state["cols"])
        rows = [r[:k] + [""]*(k-len(r)) if len(r) < k else r[:k] for r in rows]
        df_new = pd.DataFrame(rows, columns=st.session_state["cols"])
    else:
        df_new = pd.DataFrame(rows)
    # convertir tipos
    for c in df_new.columns:
        if c.lower() in ("timestamp", "time", "fecha", "datetime"):
            df_new[c] = pd.to_datetime(df_new[c], errors="coerce")
        else:
            df_new[c] = pd.to_numeric(df_new[c], errors="coerce")
    # filtrar claves
    if col_time in df_new.columns:
        df_new = df_new[~df_new[col_time].isna()]
    if col_I in df_new.columns:
        df_new = df_new[~df_new[col_I].isna()]
    return df_new, None

df_new, err = leer_lineas_nuevas(ruta, sep, tiene_header)
status = st.empty()
if err:
    status.warning(err)
else:
    if not df_new.empty:
        st.session_state["df"] = pd.concat([st.session_state["df"], df_new], ignore_index=True)
        if max_rows and len(st.session_state["df"]) > max_rows:
            st.session_state["df"] = st.session_state["df"].iloc[-max_rows:].reset_index(drop=True)
    status.info(f"Filas totales: {len(st.session_state['df'])} · Última lectura: {time.strftime('%H:%M:%S')}")

df = st.session_state["df"].copy()
if not tiene_header and not df.empty:
    if df.shape[1] >= 2:
        df.columns = [col_time, col_I] + [f"col{i}" for i in range(3, df.shape[1]+1)]
    else:
        st.error("Archivo sin encabezado necesita al menos 2 columnas (timestamp, corriente).")
        st.stop()

# ===================== Métricas trafo
m1, m2 = st.columns(2)
m1.metric("Potencia nominal", f"{kva:.1f} kVA")
m2.metric("I_nom (calc., 380 V tri)", f"{I_nom_auto:.2f} A")

# ===================== Temperatura ambiente (Open-Meteo + caché u CSV)
CACHE_DIR = "cache_temp"
os.makedirs(CACHE_DIR, exist_ok=True)

def cache_path(lat: float, lon: float, day: date) -> str:
    return os.path.join(CACHE_DIR, f"temps_{day.strftime('%Y-%m-%d')}_{lat:.2f}_{lon:.2f}.csv")

@st.cache_data(show_spinner=True)
def fetch_openmeteo_hourly(lat: float, lon: float, day: date) -> pd.DataFrame:
    cpath = cache_path(lat, lon, day)
    if os.path.exists(cpath):
        dfc = pd.read_csv(cpath)
        dfc["timestamp"] = pd.to_datetime(dfc["timestamp"])
        return dfc.sort_values("timestamp")
    base = "https://archive-api.open-meteo.com/v1/era5"
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": day.strftime("%Y-%m-%d"),
        "end_date": day.strftime("%Y-%m-%d"),
        "hourly": "temperature_2m",
        "timezone": "America/Argentina/Cordoba",
    }
    r = requests.get(base, params=params, timeout=30)
    r.raise_for_status()
    js = r.json()
    times = js["hourly"]["time"]
    temps = js["hourly"]["temperature_2m"]
    dfh = pd.DataFrame({"timestamp": pd.to_datetime(times), "temp_c": temps}).sort_values("timestamp")
    dfh.to_csv(cpath, index=False)
    return dfh

temp_df = None
if temp_source == "Open-Meteo (automático)":
    try:
        temp_df = fetch_openmeteo_hourly(lat, lon, fecha_sel)
    except Exception as e:
        st.error(f"No se pudo obtener temperatura ambiente: {e}")
else:
    if up_temp is not None:
        tdf = pd.read_csv(up_temp)
        tdf.columns = [c.lower() for c in tdf.columns]
        if "datetime" in tdf.columns and "timestamp" not in tdf.columns:
            tdf.rename(columns={"datetime": "timestamp"}, inplace=True)
        if "temperatura" in tdf.columns and "temp_c" not in tdf.columns:
            tdf.rename(columns={"temperatura": "temp_c"}, inplace=True)
        tdf["timestamp"] = pd.to_datetime(tdf["timestamp"], errors="coerce")
        tdf["temp_c"] = pd.to_numeric(tdf.get("temp_c", np.nan), errors="coerce")
        temp_df = tdf.dropna(subset=["timestamp","temp_c"]).sort_values("timestamp")

# Alinear temperatura (obligatoria)
if not df.empty and temp_df is not None and not temp_df.empty:
    df = df.copy().sort_values(col_time)
    df = pd.merge_asof(
        df.rename(columns={col_time: "timestamp"}),
        temp_df.sort_values("timestamp"),
        on="timestamp",
        direction="nearest",
        tolerance=pd.Timedelta("45min")
    ).rename(columns={"timestamp": col_time})
    if df["temp_c"].isna().any():
        df["temp_c"] = df["temp_c"].interpolate(limit_direction="both")
else:
    st.error("La temperatura ambiente es obligatoria. Elegí Open-Meteo o subí un CSV válido.")
    st.stop()

# ===================== Tabla del día
st.subheader("📋 Datos del día (últimas 288 filas ≈ 24 h a 5 min)")
if not df.empty:
    st.dataframe(
        df[[col_time, col_I, "temp_c"]].tail(288),
        use_container_width=True, height=280
    )
else:
    st.caption("Esperando datos…")

# ===================== Guardar CSV (DEBAJO de la tabla)
st.markdown("---")
st.subheader("💾 Guardar CSV con temperatura y cálculo térmico")

carpeta_guardado = st.text_input(
    "📁 Carpeta destino",
    value=r"C:\Users\NoteBook\Desktop\Demo\Txt_Datos",
    help="Carpeta local donde se guardará el CSV combinado."
)
nombre_archivo = f"Trafo_{fecha_sel.strftime('%Y-%m-%d')}.csv"
ruta_completa = os.path.join(carpeta_guardado, nombre_archivo)

# Crear variable vacía por si todavía no se generó df_dyn
if "df_dyn" not in st.session_state:
    st.session_state["df_dyn"] = pd.DataFrame()

# Crear carpeta si no existe
if not os.path.exists(carpeta_guardado):
    try:
        os.makedirs(carpeta_guardado)
        st.info(f"Se creó la carpeta: {carpeta_guardado}")
    except Exception as e:
        st.error(f"No se pudo crear la carpeta: {e}")

# Botón para guardar CSV
if st.button("🟢 Guardar CSV combinado"):
    try:
        if not st.session_state["df_dyn"].empty:
            df_export = st.session_state["df_dyn"]
        elif not df.empty:
            df_export = df
        else:
            st.error("No hay datos disponibles para guardar.")
            st.stop()

        df_export.to_csv(ruta_completa, index=False, encoding="utf-8-sig")
        st.success(f"Archivo guardado correctamente en:\n`{ruta_completa}`")
    except Exception as e:
        st.error(f"Error al guardar: {e}")

# ===================== (Más abajo) Estimación térmica + vida útil
st.markdown("---")
st.markdown("## 🔥 Estimación térmica (dinámica) y pérdida de vida")

# Parámetros (6 filas) con abreviaturas + explicación
st.markdown("### Parámetros térmicos del modelo")

I_nom = st.number_input(
    "I_nom [A] — Corriente nominal calculada",
    value=float(st.session_state.get("I_nom_auto", 36.0)),
    step=0.5,
    help="Corriente nominal del transformador. Se calcula automáticamente a partir de la potencia en kVA (380 V trifásico)."
)

expo = st.number_input(
    "n — Exponente térmico del modelo",
    value=0.8,
    step=0.05,
    help="Define cómo varía la temperatura interna con la carga. Usualmente entre 0.8 y 1.0."
)

dTO_nom = st.number_input(
    "Δθ_TO,nom [°C] — Elevación de temperatura del aceite a carga nominal",
    value=55.0,
    step=1.0,
    help="Incremento de temperatura promedio del aceite respecto al ambiente bajo carga nominal."
)

dH_nom = st.number_input(
    "Δθ_H,nom [°C] — Gradiente de hot-spot sobre el aceite a carga nominal",
    value=30.0,
    step=1.0,
    help="Diferencia entre la temperatura del punto caliente (hot-spot) y la del aceite en régimen nominal."
)

tau_TO_min = st.number_input(
    "τ_TO [min] — Constante de tiempo térmica del aceite",
    value=120.0,
    step=5.0,
    help="Tiempo característico de respuesta térmica del aceite del transformador (típicamente 60 a 180 minutos)."
)

tau_H_min = st.number_input(
    "τ_H [min] — Constante de tiempo térmica del devanado",
    value=6.0,
    step=1.0,
    help="Tiempo característico de respuesta térmica del devanado (típicamente 4 a 10 minutos)."
)

# ===== Cálculo dinámico + vida (usa temp_c obligatoria)
df_dyn = df.copy()
if not df_dyn.empty:

    # Tiempos
    df_dyn[col_time] = pd.to_datetime(df_dyn[col_time], errors="coerce")
    df_dyn = df_dyn.dropna(subset=[col_time, col_I, "temp_c"]).sort_values(col_time)
    df_dyn["dt_min"] = df_dyn[col_time].diff().dt.total_seconds().div(60).fillna(0)
    med = df_dyn["dt_min"].replace(0, np.nan).median()
    df_dyn.loc[df_dyn["dt_min"] <= 0, "dt_min"] = med if not np.isnan(med) else 5.0

    # Objetivos en régimen
    df_dyn["K"] = (df_dyn[col_I] / max(I_nom, 1e-6)).clip(lower=0)
    df_dyn["dTO_inf"] = dTO_nom * (df_dyn["K"]**2) ** expo
    df_dyn["dH_inf"]  = dH_nom  * (df_dyn["K"]**2) ** expo

    # Recurrencias
    dTO = 0.0; dH = 0.0
    dTO_list, dH_list = [], []
    for dtm, dTOi, dHi in zip(df_dyn["dt_min"].values, df_dyn["dTO_inf"].values, df_dyn["dH_inf"].values):
        a_TO = 1 - np.exp(-(dtm / max(tau_TO_min, 1e-6)))
        a_H  = 1 - np.exp(-(dtm / max(tau_H_min,  1e-6)))
        dTO = dTO + (dTOi - dTO) * a_TO
        dH  = dH  + (dHi  - dH ) * a_H
        dTO_list.append(dTO); dH_list.append(dH)
    df_dyn["dTO_dyn"] = dTO_list
    df_dyn["dH_dyn"]  = dH_list

    # Hot-spot y FAA
    df_dyn["theta_A"] = df_dyn["temp_c"]
    df_dyn["theta_H"] = df_dyn["theta_A"] + df_dyn["dTO_dyn"] + df_dyn["dH_dyn"]
    thetaK = df_dyn["theta_H"] + 273.0
    df_dyn["FAA"] = np.exp(15000.0/383.0 - 15000.0 / thetaK.clip(lower=1.0))

    # KPIs
    vida_horas_eq = float((df_dyn["FAA"] * (df_dyn["dt_min"]/60.0)).sum())
    vida_pct_dia  = (vida_horas_eq / 24.0) * 100.0
    k1, k2, k3 = st.columns(3)
    k1.metric("θ_H pico [°C]", f"{df_dyn['theta_H'].max():.1f}")
    k2.metric("FAA medio (día)", f"{df_dyn['FAA'].mean():.2f}")
    k3.metric("Vida eq. [h] · % día", f"{vida_horas_eq:,.2f} · {vida_pct_dia:,.2f} %")

    # Gráficas (sin 'Temp vs Hot-Spot', a pedido)
    st.markdown("### 📊 Gráficas")
    figI = px.line(df_dyn, x=col_time, y=col_I, title="Corriente [A]")
    figI.update_layout(template="plotly_white", height=300)
    st.plotly_chart(figI, use_container_width=True)

    figF = px.line(df_dyn, x=col_time, y="FAA", title="Factor de Envejecimiento (Arrhenius)")
    figF.update_layout(template="plotly_white", height=300)
    st.plotly_chart(figF, use_container_width=True)

# ===================== Auto-refresh
if auto:
    time.sleep(intervalo)
    st.rerun()




