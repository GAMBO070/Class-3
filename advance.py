import os
import re
import time
import asyncio
import threading
import edge_tts
import subprocess
import streamlit as st
import streamlit.components.v1 as components

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Force LGPIO Pin Factory for Raspberry Pi OS
from gpiozero.pins.lgpio import LGPIOFactory
from gpiozero import Device, Motor

Device.pin_factory = LGPIOFactory()

from groq import Groq

# --- 1. SETUP GROQ CLIENT & VOICE ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
client = Groq(api_key=GROQ_API_KEY)

FEMALE_VOICE = "en-US-AvaNeural"

def clean_speech_text(text: str) -> str:
    """Cleans LaTeX, code syntax, and math formatting so speech sounds natural."""
    # Convert common LaTeX math commands to spoken words
    text = re.sub(r'\\times', ' times ', text)
    text = re.sub(r'\\div', ' divided by ', text)
    text = re.sub(r'\\pm', ' plus or minus ', text)
    text = re.sub(r'\\cdot', ' times ', text)
    text = re.sub(r'\\approx', ' approximately ', text)
    text = re.sub(r'\\leq', ' less than or equal to ', text)
    text = re.sub(r'\\geq', ' greater than or equal to ', text)
    text = re.sub(r'\\neq', ' not equal to ', text)
    
    # Strip markdown symbols, backslashes, and math dollar signs
    text = re.sub(r'[\*\#\_`\$\\\\]', '', text)
    
    # Clean up redundant spaces
    return re.sub(r'\s+', ' ', text).strip()

async def speak_full_text(text):
    """Generates the audio response and plays it cleanly out loud via 3.5mm jack."""
    speech_text = clean_speech_text(text)
    if not speech_text:
        return

    temp_audio = f"/tmp/nova_response_{int(time.time())}.mp3"
    try:
        # Boost volume by +50% in edge-tts
        communicate = edge_tts.Communicate(speech_text, FEMALE_VOICE, rate="+15%", volume="+50%")
        await communicate.save(temp_audio)

        # Explicit ALSA Card 2 (Headphones 3.5mm jack) routing with software amplification
        subprocess.run(
            [
                "mpv",
                "--no-terminal",
                "--ao=alsa",
                "--audio-device=alsa/hw:CARD=Headphones,DEV=0",
                "--volume=150",
                temp_audio
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"Audio Error: {e}")
    finally:
        if os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except OSError:
                pass

# --- 2. INITIALIZE MOTORS (CACHED TO PREVENT GPIOPinInUse ERROR) ---
@st.cache_resource
def get_motors():
    """Initializes motors only ONCE to prevent GPIO lock errors across Streamlit reruns."""
    left = Motor(forward=17, backward=18, pwm=False)
    right = Motor(forward=22, backward=23, pwm=False)
    return left, right


motor_left, motor_right = get_motors()

def move_motors(direction):
    """Executes motor movements directly over local GPIO."""
    if direction == "forward":
        motor_left.forward()
        motor_right.forward()
    elif direction == "backward":
        motor_left.backward()
        motor_right.backward()
    elif direction == "left":
        motor_left.backward()
        motor_right.forward()
    elif direction == "right":
        motor_left.forward()
        motor_right.backward()
    elif direction == "stop":
        motor_left.stop()
        motor_right.stop()

# --- 3. LOCAL MOTOR SERVER ---
MOTOR_SERVER_PORT = 8502

class MotorRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path)
        d = parse_qs(p.query).get("dir", [""])[0]
        if p.path == "/move" and d in ("forward", "backward", "left", "right"):
            move_motors(d)
        elif p.path == "/stop":
            move_motors("stop")
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def log_message(self, format, *args):
        return

@st.cache_resource
def start_motor_server():
    server = ThreadingHTTPServer(("0.0.0.0", MOTOR_SERVER_PORT), MotorRequestHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

start_motor_server()

# --- 4. SYSTEM PROMPT WITH EXPANDED, BALANCED LENGTH AND FRIENDLY TONE ---
SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "You are Nova, an encouraging and friendly female STEM teacher robot for school students.\n\n"
        "Tone and Teaching Style:\n"
        "- Speak in a warm, welcoming, and easy-to-understand conversational tone.\n"
        "- Give clear, direct explanations with a helpful real-life example or quick breakdown so students truly understand.\n\n"
        "Strict Formatting Rules:\n"
        "1. Write equations in standard plain text (e.g., '5 x 6 = 30' or '5 times 6 equals 30'). NEVER use LaTeX notation, backslashes, or dollar signs ($).\n"
        "2. Response Length: By default, provide a balanced answer across TWO well-developed paragraphs (neither too brief nor overwhelming).\n"
        "3. Detailed Inquiries: If the student specifically asks for 'in detail' or 'explain more', provide THREE to FOUR clear paragraphs.\n"
        "4. Subject Boundary: Strictly answer educational and academic topics. Politely decline harmful, illegal, or non-educational prompts.\n"
        "5. Always bring your final sentence to a full, natural conclusion without stopping midway."
        "6. if you are ask who created you, your answer should be Amina Sada Mainasara,Affan Usman Abubakar Zaria,Mujaheed Sada Mainasara and Umar Muhammad  our Teacher"
    )
}

if "messages" not in st.session_state:
    st.session_state.messages = []

# --- 5. STREAMLIT PAGE CONFIG ---
st.set_page_config(page_title="Nova Robot Interface", layout="centered")

# --- 6. SIDEBAR: AUDIO TOGGLE & MOTOR CONTROLS ---
DPAD_HTML = '''
<style>
  .dpad{display:grid;grid-template-columns:repeat(3,60px);grid-template-rows:repeat(3,60px);gap:6px;justify-content:center}
  .dpad button{font-size:20px;touch-action:none;user-select:none;-webkit-user-select:none;border-radius:8px;border:1px solid #ccc;background:#f0f0f0}
  .dpad button:active{background:#999;color:white}
  .fwd{grid-column:2;grid-row:1}
  .left{grid-column:1;grid-row:2}
  .right{grid-column:3;grid-row:2}
  .bwd{grid-column:2;grid-row:3}
</style>

<div class="dpad">
  <button class="fwd" data-dir="forward">&#9650;</button>
  <button class="left" data-dir="left">&#9664;</button>
  <button class="right" data-dir="right">&#9654;</button>
  <button class="bwd" data-dir="backward">&#9660;</button>
</div>

<script>
(function () {
  var HOST = "localhost";
  try { HOST = window.parent.location.hostname || HOST; } catch (e) {}
  var BASE = "http://" + HOST + ":__PORT__";

  function send(p) { fetch(BASE + p, { mode: "no-cors" }).catch(function () {}); }

  document.querySelectorAll("[data-dir]").forEach(function (btn) {
    var dir = btn.dataset.dir;
    btn.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      send("/move?dir=" + dir);
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach(function (evt) {
      btn.addEventListener(evt, function () { send("/stop"); });
    });
  });

  window.addEventListener("blur", function () { send("/stop"); });
})();
</script>
'''.replace("__PORT__", str(MOTOR_SERVER_PORT))

with st.sidebar:
    st.title("Settings & Controls")

    # Audio Toggle Option
    st.subheader("🔊 Audio Settings")
    speaker_enabled = st.toggle("Enable Voice Output", value=True)
    if speaker_enabled:
        st.caption("Speaker is ACTIVE 🔊")
    else:
        st.caption("Speaker is MUTED 🔇")

    st.divider()

    # Motor Controls
    st.subheader("🎮 Motor Controls")
    st.caption("Hold a direction to move — release to stop")
    components.html(DPAD_HTML, height=200)

# --- 7. MAIN CHAT INTERFACE ---
st.title("NOVA ASSISTANT")
st.caption("STEM Teacher Tutor Robot")

# Display conversation history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# Text Input
if user_query := st.chat_input("Ask Nova an educational question..."):
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.write(user_query)

    payload = [SYSTEM_PROMPT] + st.session_state.messages[-3:]

    with st.chat_message("assistant"):
        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=payload,
            temperature=0.35,
            max_tokens=1400
        )
        
        full_response = completion.choices[0].message.content
        st.write(full_response)

    st.session_state.messages.append({"role": "assistant", "content": full_response})

    if speaker_enabled:
        asyncio.run(speak_full_text(full_response))