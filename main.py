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
import requests # YENİ: GitHub ile iletişim kurmak için
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_TOKEN = os.getenv("EZEL_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")

if not API_KEY:
    print("[UYARI] API anahtarı bulunamadı!")

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

        thumb_img = analiz_resmi.copy()
        thumb_img.thumbnail((150, 150))
        buffered = io.BytesIO()
        thumb_img.save(buffered, format="JPEG", quality=60)
        thumbnail_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
        
        hedef_talimat = f"""
        [!!! HEDEF VARYANT: {target_code} !!!]
        Kullanıcı bu resimdeki tablodan SADECE '{target_code}' varyantının/ölçüsünün analiz edilmesini istedi.""" if target_code else "Resmin antet kısmındaki ana 'KODU'nu ve 'AÇIKLAMA'yı bul."

        hafiza_talimati = f"""
        [!!! KURUMSAL HAFIZA (KESİN UYULACAK) !!!]
        {sirket_hafizasi}
        """ if sirket_hafizasi and sirket_hafizasi.strip() != "" else ""

        prompt = f"""
        Sen EZEL KALIP PLANLAMA-ÜRETİM için uzman bir üretim mühendisi ve ERP planlamacısısın.
        
        {hedef_talimat}
        {hafiza_talimati}
        
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

# --- YENİ: GITHUB VERİTABANINA ÖĞRETME SİSTEMİ ---
@app.post("/api/veritabanina-kaydet")
async def github_veritabani_kaydet(veri: dict = Body(...)):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise HTTPException(status_code=500, detail="GitHub Token veya Repo ayarlanmamış (.env dosyasını kontrol edin).")

    dosya_yolu = "ezel_kalip_db.json"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{dosya_yolu}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    try:
        # 1. Mevcut Veritabanını İndir
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            file_data = response.json()
            sha = file_data['sha']
            mevcut_icerik = base64.b64decode(file_data['content']).decode('utf-8')
            veritabani = json.loads(mevcut_icerik)
        else:
            # Dosya yoksa yeni bir liste oluştur
            sha = None
            veritabani = []

        # 2. Yeni Veriyi Ekle (Aynı parça kodu varsa güncelle, yoksa ekle)
        guncellendi = False
        for i, kayit in enumerate(veritabani):
            if kayit.get("parca_kodu") == veri.get("parca_kodu"):
                veritabani[i] = veri
                guncellendi = True
                break
        
        if not guncellendi:
            veritabani.append(veri)

        # 3. GitHub'a Geri Yükle (Commit)
        yeni_icerik_b64 = base64.b64encode(json.dumps(veritabani, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
        
        commit_mesaji = f"Veritabanı Eğitimi: {veri.get('parca_kodu')} eklendi/güncellendi."
        payload = {"message": commit_mesaji, "content": yeni_icerik_b64}
        if sha:
            payload["sha"] = sha

        put_response = requests.put(url, headers=headers, json=payload)
        
        if put_response.status_code in [200, 201]:
            return {"mesaj": "Veritabanı başarıyla güncellendi ve GitHub'a kaydedildi!"}
        else:
            raise HTTPException(status_code=put_response.status_code, detail=f"GitHub Hatası: {put_response.text}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Veritabanı kayıt hatası: {str(e)}")
