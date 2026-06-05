from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import fitz 
import io
import base64
from PIL import Image
import google.generativeai as genai
import json
import os
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=API_KEY)

app = FastAPI(title="EZEL KALIP PLANLAMA-ÜRETİM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def ana_sayfa():
    return FileResponse("index.html")

@app.post("/api/proses-olustur")
async def proses_olustur(
    file: UploadFile = File(...), 
    target_code: str = Form(""),
    sirket_hafizasi: str = Form("")
):
    try:
        contents = await file.read()
        analiz_resmi = None

        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=contents, filetype="pdf")
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=150)
            mode = "RGBA" if pix.alpha else "RGB"
            analiz_resmi = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
        else:
            analiz_resmi = Image.open(io.BytesIO(contents))

        # Arşiv için thumbnail
        thumb = analiz_resmi.copy()
        thumb.thumbnail((150, 150))
        buffered = io.BytesIO()
        thumb.save(buffered, format="JPEG", quality=60)
        thumbnail_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        model = genai.GenerativeModel('gemini-2.5-flash')
        
        prompt = f"""
        Sen EZEL KALIP için bir üretim mühendisisin. Teknik resmi incele.
        Kurumsal Hafıza: {sirket_hafizasi}
        Hedef Kod: {target_code}
        
        KURALLAR:
        1. Parça kodunun başındaki harfi (örn: U) SİL. Kalan rakamın başına işlem önekli 2 harf ekle (Örn: TE0009815).
        2. kisa_aciklama (125 karakter), uzun_aciklama (500 karakter).
        
        Sadece JSON çıktısı ver:
        {{
          "parca_kodu": "...",
          "parca_aciklamasi": "...",
          "prosesler": [ {{"canias_kodu": "TE0009815", "kisa_aciklama": "...", "uzun_aciklama": "...", "brut_kg": "0.0", "net_kg": "0.0", "fire_kg": "0.0", "sure_dk": "0.0"}} ]
        }}
        """
        
        response = model.generate_content([prompt, analiz_resmi])
        ham_metin = response.text.replace("```json", "").replace("```", "")
        return {"veri": json.loads(ham_metin), "thumbnail": thumbnail_b64}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
