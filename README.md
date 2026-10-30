VISUAL_CROSSING_API_KEY = ""
VISUAL_CROSSING_BASE_URI = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"

EPIAS_BASE_URI = "https://seffaflik.epias.com.tr/electricity-service/v1/markets/dam/"


şuanlık hava durumu verileri ve epias elektirik fiyatları çekiliyor. epiaş için saat bilgileri şöyle:

__kesinleşmemiş piyasa takas fiyatı: her gün 13.30 (kesin fiyat değildir, inceleme için)__

**kesinleşmiş piyasa takas fiyatı  : her gün 14.00 (bu fiyat esas alınır)**


# BUNLARI ALDIKTAN SONRA KESİNLİKLE SİL 

#-------------------------------------------------------------------------------------------


VISUAL_CROSSING_API_KEY = "BXG7DDYB8ANXG8L6SDC5ZNLNQ"
VISUAL_CROSSING_BASE_URI = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/"

EPIAS_BASE_URI = "https://seffaflik.epias.com.tr/electricity-service/v1/markets/dam/"


#-------------------------------------------------------------------------------------------
# FORECAST.PY AYARLARI

PANEL_COUNT = 70
PANEL_POWER_WP = 500

INSTALLED_CAPACITY_KWP = (
    PANEL_COUNT * PANEL_POWER_WP / 1000
)

ENLEM = 39.977069743745155
BOYLAM = 32.68781801235893
PERFORMANCE_RATIO = 0.85
DAILY_ENERGY = 0.0
KATSAYI = 1000
PANEL_TILT = 30 #panel eğimi
PANEL_AZIMUTH = 180 #panel yönü (güney)
ALBEDO = 0.20 # yer yüzeyi yansıtıcı


#-------------------------------------------------------------------------------------------
