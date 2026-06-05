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

if not API_KEY:
    print("[UYARI] API anahtarı bulunamadı, sistem değişkenleri kontrol ediliyor...")

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

        # --- MİNİ GÖRSEL (THUMBNAIL) OLUŞTURMA ---
        thumb_img = analiz_resmi.copy()
        thumb_img.thumbnail((150, 150))
        buffered = io.BytesIO()
        thumb_img.save(buffered, format="JPEG", quality=60)
        thumbnail_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        hedef_talimat = f"""
        [!!! HEDEF VARYANT: {target_code} !!!]
        1. Kullanıcı bu resimdeki tablodan SADECE '{target_code}' varyantının/ölçüsünün analiz edilmesini istedi. Tablodaki diğer satırları tamamen YOK SAY!""" if target_code else """
        1. Resmin antet kısmındaki ana "KODU"nu bul.
        2. Antetteki "AÇIKLAMA" veya parça adını bul."""

        hafiza_talimati = f"""
        [!!! KURUMSAL HAFIZA (KESİN UYULACAK) !!!]
        Aşağıdaki kurallar bizzat mühendis tarafından yazılmıştır. Prosesi oluştururken bu kuralları ASLA çiğneme ve hesaplamalarını buna göre yap:
        {sirket_hafizasi}
        """ if sirket_hafizasi and sirket_hafizasi.strip() != "" else ""

        prompt = f"""
        Sen EZEL KALIP PLANLAMA-ÜRETİM için uzman bir üretim mühendisi ve ERP planlamacısısın. Sana verilen teknik resmi dikkatlice incele.
        
        {hedef_talimat}
        {hafiza_talimati}
        
        3. Parçanın şekline, malzemesine ve toleranslarına bakarak mantıklı bir imalat proses rotası çıkar.
        4. Her operasyon için tahmini Brüt Kg, Net Kg, Fire Kg ve İşlem Süresi (DK) belirle.
        5. "KONUŞAN KOD" SİSTEMİ (ÇOK ÖNEMLİ): Orijinal parça kodunun başındaki harfi (örn: 'U') tamamen SİL. Sadece rakamlar kalsın. Bu rakamların başına operasyonu temsil eden 2 HARFLİ bir önek ekle. (Örn: Parça U0009815 ve işlem Testere ise kod TE0009815 olmalı, Torna ise TO0009815 olmalı).
        6. [AÇIKLAMALAR ÇİFTLENDİ]: Her operasyon için İKİ farklı açıklama yazacaksın:
           - "kisa_aciklama": ERP'ye kaydedilecek, operasyonu net özetleyen MAKSİMUM 125 KARAKTERLİK metin.
           - "uzun_aciklama": Operatörün tezgahta okuyacağı, dikkat edilecek hassasiyetleri ve detayları içeren MAKSİMUM 500 KARAKTERLİK talimat.
        
        Sadece JSON formatında çıktı ver:
        {{
          "parca_kodu": "BULUNAN_KOD",
          "parca_aciklamasi": "BULUNAN_AÇIKLAMA",
          "prosesler": [
            {{
              "canias_kodu": "TE0009815",
              "kisa_aciklama": "125 karaktere kadar kısa özet",
              "uzun_aciklama": "500 karaktere kadar detaylı operatör talimatı",
              "brut_kg": "0.00",
              "net_kg": "0.00",
              "fire_kg": "0.00",
              "sure_dk": "0.0"
            }}
          ]
        }}
        """
        
        # --- KOTA AŞIMI (429) İÇİN DİNAMİK MODEL YEDEKLEME ---
        modeller = ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash']
        response = None
        hata_mesaji = ""
        
        for model_adi in modeller:
            try:
                model = genai.GenerativeModel(model_adi)
                response = model.generate_content([prompt, analiz_resmi])
                break 
            except Exception as e:
                hata_mesaji = str(e)
                print(f"[UYARI] {model_adi} başarısız: {hata_mesaji}. Diğerine geçiliyor...")
                continue 
                
        if not response:
            raise ValueError(f"Tüm modellerin kotası doldu veya hata oluştu. {hata_mesaji}")

        ham_metin = response.text
        
        # --- TOKEN VE MALİYET HESAPLAMA ---
        try:
            p_tokens = response.usage_metadata.prompt_token_count
            c_tokens = response.usage_metadata.candidates_token_count
            t_tokens = response.usage_metadata.total_token_count
            maliyet = (p_tokens / 1_000_000 * 0.075) + (c_tokens / 1_000_000 * 0.30)
            maliyet_str = f"${maliyet:.5f}"
        except Exception:
            t_tokens = 0
            maliyet_str = "$0.00000"
        
        ilk_parantez = ham_metin.find('{')
        son_parantez = ham_metin.rfind('}')
        
        if ilk_parantez != -1 and son_parantez != -1:
            temiz_json = ham_metin[ilk_parantez:son_parantez+1]
            sonuc_verisi = json.loads(temiz_json)
        else:
            raise ValueError("Yapay zeka JSON formatında yanıt vermedi.")

        return {"mesaj": "Başarılı", "veri": sonuc_verisi, "thumbnail": thumbnail_b64, "token": t_tokens, "maliyet": maliyet_str}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sunucu hatası: {str(e)}")
