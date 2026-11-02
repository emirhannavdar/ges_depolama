# ScadaWatt EMS Dashboard — v0.2

React + TypeScript + Vite frontend for the existing `ges_tahmin` FastAPI / simulator app.

## What's new in v0.2

- Warm editorial interface: muted amber solar, teal battery, green economic upside, rust purchases, blue PTF data; keeps the minimalist brutalist framework.
- Above-the-fold economic impact comparison, with real output fields from `POST /optimize` (net delta after modeled degradation, baseline value, import cost, export revenue). Negative numbers represent net grid costs, not necessarily errors.
- Contextual decision explanation for the matching simulated hour. Uses plan and telemetry; is **rule-based explanatory copy**, not an additional AI optimizer. Actual physical control/PCS decisions remain simulator controlled.
- Paired EPİAŞ PTF chart, forecast generation bars, daily production breakdown and price peak for the **available optimization period only**.
- Preserves current API and WebSocket setup: `/api` Vite proxy → FastAPI on `127.0.0.1:8000`; WebSocket connects directly to `ws://127.0.0.1:8000/simulation/ws` in dev.
- Responsive CSS, semantic and focus states, reduced-motion support, inline form validation and scroll progress.

## Run (Windows PowerShell)

Run backend with `SIMULATION_DATABASE_URL` set in the **same** shell as uvicorn:

```powershell
python -m uvicorn api.api:app --host 127.0.0.1 --port 8000 --reload
```

In a separate PowerShell terminal:

```powershell
cd ges-ems-dashboard
npm install
npm run dev
```

Visit http://127.0.0.1:5173

Open Optimization and select a date with matching real EPİAŞ data and available weather forecast, then calculate and start the simulation. The dashboard economic comparison reflects the **last optimization calculated in this browser tab**, and is reset on refresh; historical session economics require a future API enhancement.

## Important data limits

- No mock live measurements, PTF prices, or forecast figures are generated. No pricing contract mode in v0.2.
- Price references are EPİAŞ PTF. Import cost is calculated from the PTF-based *model*, not a final billed tariff. This is not a claim of realized profit.
- Production bars are `generation_kwh` output from the weather/pvlib forecast model, not historical meter readings.
- 7-day forecasts are **not** synthesized; only hours fetched and optimized by the API are shown.
- This UI does not modify the backend or resolve the earlier `solar_origin_stored_kwh` accounting issue; it deliberately does not show that field.

## Validation

JSX/TypeScript syntax transpilation checked with TypeScript; full `npm run build` could not be executed in the packaging environment because npm dependency download timed out. Please run `npm run build` in your local project and share any errors.


## v0.2.1 düzeltmeleri

- Tarihler veya senaryo parametreleri değişince önceki optimizasyon sonucu kaldırılır.
- `/optimize` yanıtı istenen tarih aralığının dışına çıkıyorsa, döndürülen dönem farklıysa veya saat sayısı eksikse eski/eksik kâr sonucu gösterilmez; hata açıklanır. Çok günlük analiz için backend'in o günlerin tüm saatlerini döndürmesi gerekir.
- EPİAŞ PTF çizgisinin üzerine gelince ve klavyeyle noktalara odaklanınca saatlik fiyat, üretim, tüketim, batarya kararı, şarj/deşarj, alış/satış ve SOC ayrıntıları görünür.
- GES üretim histogramındaki çubukların üzerine gelince saatlik fiyat, üretim, tüketim ve karar bilgisi tarayıcı ipucunda gösterilir.
- Gösterilen ekonomik fark *bataryanın bataryasız senaryoya göre ek katkısıdır*, toplam net gelir değildir.

**Önemli:** Aynı tarih aralığı dışında gelen 24 saatlik sonuç frontend tarafından reddedilir. Bu gerçek backend fiyat/tahmin tarih filtreleme hatasını tek başına onarmaz. Backend dönem bazlı çıktı üretmiyorsa onun da düzeltilmesi gerekecek.
