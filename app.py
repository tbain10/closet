import streamlit as st
import requests
from PIL import Image
import io
import base64
import json
from supabase import create_client, Client
import anthropic
import time
from datetime import datetime
from streamlit_geolocation import streamlit_geolocation

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="My Closet & Weather",
    page_icon="🕴",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .weather-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px; border-radius: 15px; color: white;
        text-align: center; margin-bottom: 16px;
    }
    .weather-card h2 { font-size: 3rem; margin: 0; }
    .weather-card p  { margin: 4px 0; opacity: 0.9; }
    .stButton > button { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ── Supabase ──────────────────────────────────────────────────────────────────
@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])

supabase = get_supabase()
BUCKET = "closet"

# ── Weather ───────────────────────────────────────────────────────────────────
def city_from_coords(lat: float, lon: float) -> str:
    try:
        r = requests.get(
            "https://api.openweathermap.org/geo/1.0/reverse",
            params={"lat": lat, "lon": lon, "limit": 1, "appid": st.secrets["OPENWEATHER_API_KEY"]},
            timeout=5,
        )
        data = r.json()
        return data[0]["name"] if data else "New York"
    except Exception:
        return "New York"

def get_weather(city: str) -> dict | None:
    url = "https://api.openweathermap.org/data/2.5/weather"
    r = requests.get(url, params={"q": city, "appid": st.secrets["OPENWEATHER_API_KEY"], "units": "imperial"}, timeout=5)
    return r.json() if r.status_code == 200 else None

def outfit_advice(temp: float) -> str:
    if temp > 85:
        return "Super hot! Shorts, tank tops, light fabrics, and sandals."
    elif temp > 70:
        return "Warm & pleasant. T-shirts, light pants, or a sundress."
    elif temp > 55:
        return "Mild. A light jacket or long sleeves would be great."
    elif temp > 40:
        return "Cool. Layer up — sweater or hoodie recommended."
    else:
        return "Cold! Coat, scarf, warm layers — bundle up."

# ── Supabase Storage ──────────────────────────────────────────────────────────
def upload_to_supabase(image_bytes: bytes, path: str) -> str | None:
    try:
        supabase.storage.from_(BUCKET).upload(
            path, image_bytes,
            {"content-type": "image/jpeg", "x-upsert": "true"},
        )
        return supabase.storage.from_(BUCKET).get_public_url(path)
    except Exception as e:
        st.error(f"Upload failed: {e}")
        return None

def save_clothing_record(name: str, category: str, image_url: str, path: str):
    supabase.table("clothing").insert({
        "name": name, "category": category,
        "image_url": image_url, "path": path,
    }).execute()

def get_clothing() -> list[dict]:
    return supabase.table("clothing").select("*").order("created_at", desc=True).execute().data

def delete_clothing(item_id: int, path: str):
    supabase.table("clothing").delete().eq("id", item_id).execute()
    supabase.storage.from_(BUCKET).remove([path])

def profile_photo_url() -> str | None:
    try:
        return supabase.storage.from_(BUCKET).get_public_url("profile/me.jpg")
    except Exception:
        return None

# ── Virtual Try-On ────────────────────────────────────────────────────────────
def run_virtual_tryon(human_url: str, garment_url: str, garment_type: str) -> str:
    headers = {
        "Authorization": f"Bearer {st.secrets['REPLICATE_API_TOKEN']}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }
    payload = {
        "input": {
            "human_img": human_url,
            "garm_img": garment_url,
            "garment_des": f"clothing item ({garment_type.replace('_', ' ')})",
            "is_checked": True,
            "is_checked_crop": False,
            "denoise_steps": 30,
            "seed": 42,
            "category": garment_type,
        }
    }
    r = requests.post(
        "https://api.replicate.com/v1/models/cuuupid/idm-vton/predictions",
        headers=headers,
        json=payload,
        timeout=120,
    )
    r.raise_for_status()
    prediction = r.json()

    if prediction.get("status") == "succeeded":
        output = prediction["output"]
        return output[0] if isinstance(output, list) else output

    # Poll until done
    poll_url = prediction["urls"]["get"]
    for _ in range(60):
        time.sleep(3)
        poll = requests.get(poll_url, headers=headers, timeout=10).json()
        if poll["status"] == "succeeded":
            output = poll["output"]
            return output[0] if isinstance(output, list) else output
        if poll["status"] in ("failed", "canceled"):
            raise Exception(f"Prediction {poll['status']}: {poll.get('error')}")

    raise Exception("Timed out waiting for try-on result.")

# ── Smart Fit ─────────────────────────────────────────────────────────────────
@st.cache_resource
def get_anthropic_client():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

def _image_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode()

def analyze_body_proportions(image_url: str) -> dict:
    img_bytes = requests.get(image_url, timeout=10).content
    response = get_anthropic_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _image_b64(img_bytes)}},
                {"type": "text", "text": (
                    "Analyze this full-body photo and estimate clothing-relevant body proportions. "
                    "Return ONLY a JSON object with these keys: "
                    "body_type (slim/athletic/average/fuller), "
                    "shoulder_width (narrow/average/broad), "
                    "torso_length (short/average/long), "
                    "hip_width (narrow/average/wide), "
                    "height_estimate (petite/average/tall), "
                    "build_notes (1–2 sentences on proportions relevant to fit)."
                )}
            ]
        }]
    )
    return json.loads(response.content[0].text)

def read_garment_tag(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    response = get_anthropic_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": _image_b64(image_bytes)}},
                {"type": "text", "text": (
                    "Read this clothing tag and extract all sizing information. "
                    "Return ONLY a JSON object with these keys (null if not found): "
                    "size_label, chest_cm, waist_cm, hip_cm, length_cm, inseam_cm, "
                    "brand, garment_type, fabric, raw_text."
                )}
            ]
        }]
    )
    return json.loads(response.content[0].text)

def get_fit_report(body: dict, tag: dict) -> str:
    response = get_anthropic_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                f"Body proportions: {json.dumps(body)}\n\n"
                f"Garment tag: {json.dumps(tag)}\n\n"
                "Give a practical 3–4 sentence fit recommendation: will this size fit, "
                "which areas may be loose or tight, and one styling tip for this body type."
            )
        }]
    )
    return response.content[0].text

# ═══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    # ── Weather ────────────────────────────────────────────────────────────────
    st.header("🌤️ Today's Weather")

    loc = streamlit_geolocation()
    if loc and loc.get("latitude") and "auto_city" not in st.session_state:
        st.session_state.auto_city = city_from_coords(loc["latitude"], loc["longitude"])

    city = st.text_input("City", value=st.session_state.get("auto_city", ""))

    if city:
        weather = get_weather(city)
        if weather:
            temp        = weather["main"]["temp"]
            feels_like  = weather["main"]["feels_like"]
            humidity    = weather["main"]["humidity"]
            description = weather["weather"][0]["description"].title()
            icon        = weather["weather"][0]["icon"]
            advice      = outfit_advice(temp)

            st.markdown(f"""
            <div class="weather-card">
                <img src="https://openweathermap.org/img/wn/{icon}@2x.png" width="72"/>
                <h2>{temp:.0f}°F</h2>
                <p>{description}</p>
                <p>Feels like {feels_like:.0f}°F &nbsp;·&nbsp; Humidity {humidity}%</p>
                <p><strong>{city}</strong></p>
            </div>
            """, unsafe_allow_html=True)

            st.info(f"👗 **Outfit tip:** {advice}")
            st.session_state["weather_advice"] = advice
            st.session_state["temp"] = temp
        else:
            st.error("City not found — check spelling.")

    st.divider()

    # ── Profile Photo ──────────────────────────────────────────────────────────
    st.header("📸 Your Photo")
    st.caption("Upload a full-body photo of yourself for virtual try-on.")

    uploaded_profile = st.file_uploader(
        "Upload photo", type=["jpg", "jpeg", "png"], key="profile_uploader"
    )

    if uploaded_profile:
        img_bytes = uploaded_profile.read()
        with st.spinner("Saving..."):
            url = upload_to_supabase(img_bytes, "profile/me.jpg")
        if url:
            st.session_state["profile_url"] = url
            st.success("Photo saved!")

    # Show existing profile photo
    if "profile_url" not in st.session_state:
        saved = profile_photo_url()
        if saved:
            st.session_state["profile_url"] = saved

    if "profile_url" in st.session_state:
        st.image(st.session_state["profile_url"], caption="Your photo", use_container_width=True)
    else:
        st.caption("No photo yet.")

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ═══════════════════════════════════════════════════════════════════════════════
st.title("👗 My Virtual Closet")

tab_closet, tab_tryon, tab_fit = st.tabs(["🗄️ My Closet", "✨ Virtual Try-On", "📐 Smart Fit"])

# ── TAB 1: Closet ─────────────────────────────────────────────────────────────
with tab_closet:
    add_col, view_col = st.columns([1, 2], gap="large")

    with add_col:
        st.subheader("Add Item")
        item_name  = st.text_input("Name", placeholder="e.g. Blue linen shirt")
        category   = st.selectbox("Category", ["Tops", "Bottoms", "Dresses", "Outerwear", "Shoes", "Accessories"])
        item_image = st.file_uploader("Photo", type=["jpg", "jpeg", "png"], key="item_uploader")

        if item_image:
            st.image(item_image, use_container_width=True)

        if st.button("➕ Add to Closet", type="primary", use_container_width=True):
            if not item_name:
                st.warning("Please enter an item name.")
            elif not item_image:
                st.warning("Please upload a photo.")
            else:
                item_image.seek(0)
                img_bytes = item_image.read()
                timestamp = int(datetime.now().timestamp())
                path = f"{category.lower()}/{item_name.replace(' ', '_')}_{timestamp}.jpg"
                with st.spinner("Uploading..."):
                    url = upload_to_supabase(img_bytes, path)
                if url:
                    save_clothing_record(item_name, category, url, path)
                    st.success(f"✅ '{item_name}' added!")
                    st.rerun()

    with view_col:
        st.subheader("My Wardrobe")
        items = get_clothing()

        if not items:
            st.info("Your closet is empty — add some items to get started!")
        else:
            all_cats = ["All"] + sorted(set(i["category"] for i in items))
            filter_cat = st.selectbox("Filter", all_cats, label_visibility="collapsed")

            visible = items if filter_cat == "All" else [i for i in items if i["category"] == filter_cat]

            cols = st.columns(3)
            for idx, item in enumerate(visible):
                with cols[idx % 3]:
                    st.image(item["image_url"], use_container_width=True)
                    st.caption(f"**{item['name']}**  \n📂 {item['category']}")

                    btn_col1, btn_col2 = st.columns(2)
                    with btn_col1:
                        if st.button("Try On", key=f"try_{item['id']}", use_container_width=True):
                            st.session_state["selected_garment_id"] = item["id"]
                            st.toast(f"'{item['name']}' selected for try-on!", icon="✨")
                    with btn_col2:
                        if st.button("🗑️", key=f"del_{item['id']}", use_container_width=True, help="Delete item"):
                            delete_clothing(item["id"], item["path"])
                            st.rerun()

# ── TAB 2: Virtual Try-On ─────────────────────────────────────────────────────
with tab_tryon:
    st.subheader("✨ See Your Outfit On You")

    items = get_clothing()

    left, mid, right = st.columns(3, gap="large")

    with left:
        st.markdown("#### 1. Your Photo")
        if "profile_url" in st.session_state:
            st.image(st.session_state["profile_url"], use_container_width=True)
        else:
            st.warning("Upload your photo in the sidebar first.")

    with mid:
        st.markdown("#### 2. Pick a Garment")
        if not items:
            st.info("Add clothing to your closet first.")
        else:
            item_labels = [f"{i['name']} ({i['category']})" for i in items]

            # Pre-select if coming from "Try On" button in closet tab
            default_idx = 0
            if "selected_garment_id" in st.session_state:
                ids = [i["id"] for i in items]
                if st.session_state["selected_garment_id"] in ids:
                    default_idx = ids.index(st.session_state["selected_garment_id"])

            selected_idx = st.selectbox(
                "Garment", range(len(item_labels)),
                format_func=lambda i: item_labels[i],
                index=default_idx,
                label_visibility="collapsed",
            )
            selected = items[selected_idx]
            st.image(selected["image_url"], use_container_width=True)

            garment_type = st.radio(
                "Garment type",
                ["upper_body", "lower_body", "dresses"],
                format_func=lambda x: x.replace("_", " ").title(),
                horizontal=True,
            )

    with right:
        st.markdown("#### 3. Result")
        generate = st.button("🪄 Generate Try-On", type="primary", use_container_width=True)

        if generate:
            if "profile_url" not in st.session_state:
                st.error("Upload your photo in the sidebar first!")
            elif not items:
                st.error("Add clothing to your closet first!")
            else:
                with st.spinner("Generating… this takes ~30 seconds ⏳"):
                    try:
                        result_url = run_virtual_tryon(
                            st.session_state["profile_url"],
                            selected["image_url"],
                            garment_type,
                        )
                        st.session_state["tryon_result"] = result_url
                    except Exception as e:
                        st.error(f"Try-on failed: {e}")

        if "tryon_result" in st.session_state:
            st.image(st.session_state["tryon_result"], use_container_width=True)
            result_bytes = requests.get(st.session_state["tryon_result"]).content
            st.download_button(
                "⬇️ Download",
                data=result_bytes,
                file_name="virtual_tryon.jpg",
                mime="image/jpeg",
                use_container_width=True,
            )

# ── TAB 3: Smart Fit ──────────────────────────────────────────────────────────
with tab_fit:
    st.subheader("📐 Smart Fit — Know Before You Buy")
    st.caption("Scan a garment tag and Claude will tell you how it will fit your body.")

    fit_left, fit_right = st.columns(2, gap="large")

    with fit_left:
        st.markdown("#### Your Body Analysis")
        if "profile_url" not in st.session_state:
            st.warning("Upload your full-body photo in the sidebar first.")
        else:
            st.image(st.session_state["profile_url"], use_container_width=True)
            if st.button("🔍 Analyze My Proportions", type="primary", use_container_width=True):
                with st.spinner("Analyzing your proportions…"):
                    try:
                        body = analyze_body_proportions(st.session_state["profile_url"])
                        st.session_state["body_data"] = body
                    except Exception as e:
                        st.error(f"Analysis failed: {e}")

            if "body_data" in st.session_state:
                b = st.session_state["body_data"]
                st.success("Proportions captured!")
                cols = st.columns(2)
                cols[0].metric("Body Type", b.get("body_type", "—").title())
                cols[1].metric("Height", b.get("height_estimate", "—").title())
                cols[0].metric("Shoulders", b.get("shoulder_width", "—").title())
                cols[1].metric("Hips", b.get("hip_width", "—").title())
                cols[0].metric("Torso", b.get("torso_length", "—").title())
                st.info(b.get("build_notes", ""))

    with fit_right:
        st.markdown("#### Garment Tag Scanner")
        tag_image = st.file_uploader(
            "Photo of the tag", type=["jpg", "jpeg", "png"], key="tag_uploader"
        )

        if tag_image:
            st.image(tag_image, use_container_width=True)
            if st.button("📷 Read Tag", type="primary", use_container_width=True):
                tag_image.seek(0)
                tag_bytes = tag_image.read()
                mime = "image/png" if tag_image.type == "image/png" else "image/jpeg"
                with st.spinner("Reading tag…"):
                    try:
                        tag = read_garment_tag(tag_bytes, mime)
                        st.session_state["tag_data"] = tag
                    except Exception as e:
                        st.error(f"Tag reading failed: {e}")

        if "tag_data" in st.session_state:
            t = st.session_state["tag_data"]
            st.success(f"Size: **{t.get('size_label', '?')}**" + (f"  ·  {t.get('brand')}" if t.get('brand') else ""))
            measurements = {k: v for k, v in {
                "Chest": t.get("chest_cm"), "Waist": t.get("waist_cm"),
                "Hip": t.get("hip_cm"), "Length": t.get("length_cm"),
                "Inseam": t.get("inseam_cm"),
            }.items() if v}
            if measurements:
                mcols = st.columns(len(measurements))
                for col, (label, val) in zip(mcols, measurements.items()):
                    col.metric(label, f"{val} cm")
            if t.get("fabric"):
                st.caption(f"Fabric: {t['fabric']}")

    # Fit Report
    if "body_data" in st.session_state and "tag_data" in st.session_state:
        st.divider()
        st.markdown("#### Fit Report")
        if st.button("✨ Generate Fit Report", type="primary", use_container_width=True):
            with st.spinner("Generating your fit report…"):
                try:
                    report = get_fit_report(st.session_state["body_data"], st.session_state["tag_data"])
                    st.session_state["fit_report"] = report
                except Exception as e:
                    st.error(f"Report failed: {e}")

        if "fit_report" in st.session_state:
            st.info(st.session_state["fit_report"])
