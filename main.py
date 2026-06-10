from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import fitz 
import io
import base64
from PIL import Image
import google.generativeai as genai
import json
import os
import requests
from dotenv import load_dotenv
import pandas as pd # MASTER DATA İÇİN EKLENDİ

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
GH_TOKEN = os.getenv("EZEL_YENI_TOKEN") # HATA VERMEYEN YENİ DEĞİŞKEN ADI
GITHUB_REPO = os.getenv("GITHUB_REPO")

if not API_KEY:
    print("[UYARI] GEMINI_API_KEY bulunamadı!")
if not EZEL_YENI_TOKEN:
    print("[UYARI] GH_TOKEN bulunamadı!")
if not GITHUB_REPO:
    print("[UYARI] GITHUB_REPO bulunamadı!")

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

# --- YENİ: MASTER DATA (CSV) OKUYUCU ENDPOINT ---
@app.get("/api/kategoriler")
async def kategorileri_getir():
    try:
        if not os.path.exists('CANIASKALIPID.csv'):
            print("[UYARI] CANIASKALIPID.csv dosyası bulunamadı!")
            return []
        
        # CSV dosyasını oku (Noktalı virgül ayracıyla)
        df = pd.read_csv('CANIASKALIPID.csv', sep=';')
        
        # Boş (NaN) değerleri string ile doldur ki JSON hatası vermesin
        df = df.fillna("")
        return df.to_dict(orient="records")
    except Exception as e:
        print(f"CSV Okuma Hatası: {str(e)}")
        return []

@app.post("/api/proses-olustur")
async def proses_olustur(
    file: UploadFile = File(...), 
    target_code: str = Form(""),
    page_number: int = Form(1)
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Dosya seçilmedi")

    try:
        contents = await file.read()
        analiz_resmi = None

        if file.filename.lower().endswith(".pdf"):
            doc = fitz.open(stream=contents, filetype="pdf")
            if len(doc) > 0:
                hedef_sayfa_indeksi = page_number - 1
                if hedef_sayfa_indeksi < 0:
                    hedef_sayfa_indeksi = 0
                elif hedef_sayfa_indeksi >= len(doc):
                    hedef_sayfa_indeksi = len(doc) - 1

                page = doc.load_page(hedef_sayfa_indeksi)
                pix = page.get_pixmap(dpi=150) 
                mode = "RGBA" if pix.alpha else "RGB"
                analiz_resmi = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            else:
                raise HTTPException(status_code=400, detail="PDF okunamıyor veya boş.")
        elif file.filename.lower().endswith((".png", ".jpg", ".jpeg")):
            analiz_resmi = Image.open(io.BytesIO(contents))
        else:
            raise HTTPException(status_code=400, detail="Lütfen PDF veya resim yükleyin.")

        thumb_img = analiz_resmi.copy()
        thumb_img.thumbnail((150, 150))
        buffered = io.BytesIO()
        thumb_img.save(buffered, format="JPEG", quality=60)
        thumbnail_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        hedef_talimat = f"""
        [!!! HEDEF VARYANT: {target_code} !!!]
        Kullanıcı bu resimdeki tablodan SADECE '{target_code}' varyantının/ölçüsünün analiz edilmesini istedi.""" if target_code else "Resmin antet kısmındaki ana 'KODU'nu ve 'AÇIKLAMA'yı bul."

        prompt = f"""
        Sen EZEL KALIP PLANLAMA-ÜRETİM için uzman bir üretim mühendisi ve ERP planlamacısısın.
        
        {hedef_talimat}
        
        KURALLAR:
        1. Proses rotası çıkar. Her operasyon için tahmini Brüt Kg, Net Kg, Fire Kg ve İşlem Süresi (DK) belirle.
        2. Orijinal parça kodunun başındaki harfi (örn: 'U') tamamen SİL. Bu rakamların başına operasyonu temsil eden 2 HARFLİ bir önek ekle. (Örn: U0009815 -> Testere için TE0009815).
        3. kisa_aciklama (Maks 125 kar.), uzun_aciklama (Maks 500 kar.).
        
        Sadece JSON formatında çıktı ver:
        {{
          "parca_kodu": "...",
          "parca_aciklamasi": "...",
          "prosesler": [
            {{"canias_kodu": "TE00...", "kisa_aciklama": "...", "uzun_aciklama": "...", "brut_kg": "0.0", "net_kg": "0.0", "fire_kg": "0.0", "sure_dk": "0.0"}}
          ]
        }}
        """
        
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
                continue 
                
        if not response:
            raise ValueError(f"Kota doldu veya hata oluştu. {hata_mesaji}")

        ham_metin = response.text
        
        try:
            kur_req = requests.get("https://open.er-api.com/v6/latest/USD", timeout=3)
            usd_kur = float(kur_req.json()["rates"]["TRY"])
        except:
            usd_kur = 39.50 
            
        try:
            p_tokens = response.usage_metadata.prompt_token_count
            c_tokens = response.usage_metadata.candidates_token_count
            t_tokens = response.usage_metadata.total_token_count
            maliyet_usd = (p_tokens / 1_000_000 * 0.075) + (c_tokens / 1_000_000 * 0.30)
            maliyet_tl = maliyet_usd * usd_kur
            maliyet_str = f"${maliyet_usd:.5f} / ₺{maliyet_tl:.4f}"
        except Exception:
            t_tokens = 0
            maliyet_str = "$0.00000 / ₺0.0000"

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

@app.get("/api/veritabani")
async def github_veritabani_getir():
    if not GH_TOKEN or not GITHUB_REPO:
        return []
    
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/ezel_kalip_db.json"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            file_data = response.json()
            mevcut_icerik = base64.b64decode(file_data['content']).decode('utf-8')
            return json.loads(mevcut_icerik)
        return []
    except Exception as e:
        print("DB Çekme Hatası:", str(e))
        return []

@app.post("/api/veritabanina-kaydet")
async def github_veritabani_kaydet(veri: dict = Body(...)):
    if not GH_TOKEN:
        raise HTTPException(status_code=500, detail="HATA: GH_TOKEN sunucuda bulunamadı!")
    if not GITHUB_REPO:
        raise HTTPException(status_code=500, detail="HATA: GITHUB_REPO sunucuda bulunamadı!")

    dosya_yolu = "ezel_kalip_db.json"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{dosya_yolu}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    try:
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            file_data = response.json()
            sha = file_data['sha']
            mevcut_icerik = base64.b64decode(file_data['content']).decode('utf-8')
            veritabani = json.loads(mevcut_icerik)
        else:
            sha = None
            veritabani = []

        guncellendi = False
        for i, kayit in enumerate(veritabani):
            if kayit.get("parca_kodu") == veri.get("parca_kodu"):
                veritabani[i] = veri # Yeni veriyi üzerine yaz (kategori dahil)
                guncellendi = True
                break
        
        if not guncellendi:
            veritabani.append(veri)

        yeni_icerik_b64 = base64.b64encode(json.dumps(veritabani, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
        payload = {"message": "Ezel Kalıp DB Güncelleme (Master Data)", "content": yeni_icerik_b64}
        if sha: payload["sha"] = sha

        put_response = requests.put(url, headers=headers, json=payload)
        
        if put_response.status_code in [200, 201]:
            return {"mesaj": "Başarılı"}
        else:
            raise HTTPException(status_code=500, detail=f"GitHub Yazma Hatası: {put_response.text}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sunucu Hatası: {str(e)}")
