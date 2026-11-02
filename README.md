# ScadaWatt EMS v2.0.0 — Batarya Kayıpları ve Enerji Dengesi Denetimi

Bu sürüm v2.0.0 üzerine uygulanır. 24 saatlik sanal saha ekranı, Visual Crossing hava verisi, gerçek EPİAŞ PTF referansı, otomatik gün sonu PDF/JSON raporu, sözleşme alış/satış limitleri ve GES-only batarya şarjı korunur.

## v2.0.0 yenilikleri

- Batarya şarj ve deşarj dönüşüm kayıpları ayrı ayrı hesaplanır; toplam `battery_conversion_loss_kwh` olarak canlı durum ve rapora eklenir.
- Saha enerji dengesi; GES + şebeke alış = karşılanan tesis yükü + şebeke satışı + üretim kısıntısı + batarya dönüşüm kaybı + bataryada depolanan enerji değişimi biçiminde denetlenir. `site_energy_balance_residual_kwh` sıfıra yakın olmalıdır.
- PDF/JSON gün sonu raporunda batarya kaybı, enerji dengesi artığı ve saatlik kayıp gösterilir.
- Verilen örnek değerlerle (%95 şarj ve %95 deşarj verimi) 5.052,6 kWh şarj ve 4.560,0 kWh deşarj için yaklaşık 492,6 kWh batarya dönüşüm kaybı raporlanır.

## Kurulum (Windows / PowerShell)

Önce mevcut `api/digital_twin.py`, `api/digital_twin_report.py` ve `ges-ems-dashboard/` klasörlerini yedekleyin.

1. `backend_addon/api/digital_twin.py` ile `digital_twin_report.py` dosyalarını projenizdeki `api/` klasörüne kopyalayın.
2. ZIP içindeki `ges-ems-dashboard/` dosyalarıyla frontend klasörünü güncelleyin.
3. **Yeni backend bağımlılığı:** `python -m pip install "scipy>=1.11" numpy`
4. API: `python -m uvicorn api.api:app --reload`
5. Frontend: `cd ges-ems-dashboard; npm install; npm run dev`
6. Tarayıcıda `Ctrl+Shift+R`; **Batarya ayarları** üzerinden başlangıç %50, min %10, max %90, **hedef bitiş %50** ayarlayın. **Günün planını hazırla → 24 saati çalıştır**.

Mevcut `app.include_router(digital_twin_router)` zaten varsa tekrar eklemeyin. EPİAŞ ve Visual Crossing kimlik bilgileri eskisi gibi sunucuda kalır; arayüze yeniden girilmez.

## Test

Backend test: ZIP ana dizininde `python -m unittest discover -p 'test_*.py' -v`. Testler hedef SOC takibi, imkânsız hedef bildirimi, GES-only şarj, şebeke anlaşma sınırı, yeniden planlama ve batarya dönüşüm kayıplarıyla saha enerji dengesi denetimini sınar. Tüm gün için MILP çözücüsü gerekir.

**Önemli sınırlar:** Kontrol yalnızca **her sanal saat başında** MILP'yi yeniden çözer, her saniye değil. Hava profili gerçek saha ölçümü değil Visual Crossing'e dayalı sanal üretim modelidir. Son saatlerde oluşan üretim farkları hedef SOC'yi fiziksel olarak ulaşılamaz hale getirebilir. Zaman adımı emniyet sınırları MILP planına üstün gelir. Enerji dengesi artık değeri yuvarlama toleransı içinde sıfıra yakın olmalıdır. Karşılanamayan tesis yükünün ticari kesinti maliyeti net TL hesabına dahil değildir. Batarya çevrim ömrü fiziksel SOH modeline çevrilmez; yıpranma TL/kWh şeklindedir.
