import streamlit as st
import requests
from PIL import Image
import io
import base64
from supabase import create_client, Client
import replicate
from datetime import datetime

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="My Closet & Weather",
    page_icon="👗",
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
def get_auto_city() -> str:
    try:
        r = requests.get("https://ipapi.co/json/", timeout=4)
        return r.json().get("city", "New York")
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
    client = replicate.Client(api_token=st.secrets["REPLICATE_API_TOKEN"])
    output = client.run(
        "cuuupid/idm-vton",
        input={
            "human_img": human_url,
            "garm_img": garment_url,
            "garment_des": f"clothing item ({garment_type.replace('_', ' ')})",
            "is_checked": True,
            "is_checked_crop": False,
            "denoise_steps": 30,
            "seed": 42,
            "category": garment_type,
        },
    )
    # IDM-VTON returns a list; first item is the try-on image
    return str(output[0]) if isinstance(output, list) else str(output)

# ═══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    # ── Weather ────────────────────────────────────────────────────────────────
    st.header("🌤️ Today's Weather")

    if "auto_city" not in st.session_state:
        st.session_state.auto_city = get_auto_city()

    city = st.text_input("City", value=st.session_state.auto_city)

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

tab_closet, tab_tryon = st.tabs(["🗄️ My Closet", "✨ Virtual Try-On"])

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
