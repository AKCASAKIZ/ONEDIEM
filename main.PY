from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
import io
from PIL import Image
import google.generativeai as genai
import json
import os
from dotenv import load_dotenv

# ==========================================
# GÜVENLİ API ANAHTARI YÜKLEMESİ
# ==========================================
load_dotenv() # .env dosyasındaki gizli bilgileri okur
API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError("API Anahtarı bulunamadı! Lütfen .env dosyasını kontrol edin.")

genai.configure(api_key=API_KEY)

app = FastAPI(title="CANIAS İmalat Prosesi API")
# --------- YENİ EKLENEN KISIM: Ana Sayfayı Aç -----------
@app.get("/")
async def ana_sayfa():
    return FileResponse("index.html")
# --------------------------------------------------------

@app.post("/api/proses-olustur")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/proses-olustur")
async def proses_olustur(file: UploadFile = File(...), target_code: str = Form("")):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dosya seçilmedi")

    try:
        contents = await file.read()
        analiz_resmi = None

        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=contents, filetype="pdf")
            if len(doc) > 0:
                page = doc.load_page(0)
                pix = page.get_pixmap(dpi=150)
                mode = "RGBA" if pix.alpha else "RGB"
                analiz_resmi = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            else:
                raise HTTPException(status_code=400, detail="PDF okunamıyor.")
        elif file.filename.lower().endswith((".png", ".jpg", ".jpeg")):
            analiz_resmi = Image.open(io.BytesIO(contents))
        else:
            raise HTTPException(status_code=400, detail="Lütfen PDF veya resim yükleyin.")

        model = genai.GenerativeModel('gemini-2.0-flash')
        
        # EĞER KULLANICI TABLODAN ÖZEL BİR KOD GİRDİYSE YAPAY ZEKAYI O YÖNE KİLİTLE
        if target_code and target_code.strip() != "":
            hedef_talimat = f"""
        [!!! HEDEF VARYANT: {target_code} !!!]
        1. Kullanıcı bu resimdeki tablodan SADECE '{target_code}' varyantının/ölçüsünün analiz edilmesini istedi. Tablodaki diğer satırları tamamen YOK SAY! Sadece '{target_code}' değerine ait uzunluk, çap ve özellikleri kullanarak işlem yap.
        2. Parça kodu ve açıklaması olarak bu hedefe uygun bilgileri kullan."""
        else:
            hedef_talimat = """
        1. Resmin antet kısmındaki ana "CANIAS KODU"nu (örn: US10000028) bul.
        2. Antetteki "AÇIKLAMA" veya parça adını bul."""

        prompt = f"""
        Sen uzman bir üretim mühendisi ve ERP planlamacısısın. Sana verilen teknik resmi dikkatlice incele.
        
        {hedef_talimat}
        
        3. Parçanın şekline, malzemesine ve toleranslarına bakarak mantıklı bir imalat proses rotası çıkar.
        4. Her operasyon için tahmini Brüt Kg, Net Kg, Fire Kg ve İşlem Süresi (DK) belirle.
        5. "KONUŞAN KOD" SİSTEMİ: Operasyonun "canias_kodu" değerini oluştururken A-, B- gibi harfler YERİNE, operasyonu temsil eden 3 harfli bir önek kullan. Orijinal parça kodunun başındaki harfleri silip bu öneki ekle (Örn: Testere için TES10000028).
        
        Sadece ve sadece aşağıdaki JSON formatında çıktı ver. Başında veya sonunda hiçbir ekstra kelime kullanma:
        {{
          "parca_kodu": "BULUNAN_KOD",
          "parca_aciklamasi": "BULUNAN_AÇIKLAMA",
          "prosesler": [
            {{
              "canias_kodu": "TES10000028",
              "aciklama": "Operasyon detaylı açıklaması",
              "brut_kg": "0.00",
              "net_kg": "0.00",
              "fire_kg": "0.00",
              "sure_dk": "0.0"
            }}
          ]
        }}
        """
        
        response = model.generate_content([prompt, analiz_resmi])
        ham_metin = response.text
        
        ilk_parantez = ham_metin.find('{')
        son_parantez = ham_metin.rfind('}')
        
        if ilk_parantez != -1 and son_parantez != -1:
            temiz_json = ham_metin[ilk_parantez:son_parantez+1]
            sonuc_verisi = json.loads(temiz_json)
        else:
            print("--- YAPAY ZEKA BEKLENMEYEN CEVAP VERDİ ---")
            print(ham_metin)
            raise ValueError("Yapay zeka JSON formatında yanıt vermedi.")

        return {"mesaj": "Başarılı", "veri": sonuc_verisi}

    except Exception as e:
        print(f"\n[!!!] SUNUCU HATASI OLUŞTU: {str(e)}\n")
        raise HTTPException(status_code=500, detail=f"Sunucu hatası: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
