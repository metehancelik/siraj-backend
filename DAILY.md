# `/v1/daily` — günün ayeti, hadisi ve duası

Mobil uygulamanın "Günün Sayfası" kartlarını **seçen** uç nokta. Sözleşme burada; uygulama
tarafındaki karşılığı `siraj-mobile/src/services/` altındaki günlük içerik servisleridir.

## Neden

Bugün üç kart üç ayrı yerden geliyor ve seçim tamamen istemcide:

| İçerik | Kaynak | Seçim |
|---|---|---|
| Ayet | `api.alquran.cloud` (üçüncü taraf), Diyanet **Vakfı** meali | `(yerel gün % 6236) + 1` |
| Hadis | uygulamayla gelen `hadiths.json` (450) | `yerel gün % 450` |
| Dua | uygulamayla gelen `duas.json` (30) | `yerel gün % 30` |

Bunun üç sonucu var:

1. **Sürümler arası kayma.** Korpüs uygulamayla geldiği için 450 kayıtlı bir sürümle 500
   kayıtlı sürüm aynı gün farklı hadis gösterir. "Herkes aynı kartı görür" yalnızca aynı
   sürüm içinde doğrudur.
2. **Küratörlük yok.** Ayet seçimi Kur'an'ı baştan sona yürüyor: gün N = ayet N. Bağlamından
   koparılınca kötü okunan bir ayete denk gelebiliyor (bkz. `siraj-mobile/STORE.md`, ekran
   görüntüsü tarihinin elle seçilme sebebi). Ramazan, Cuma, kandil gibi günlere içerik
   bağlanamıyor.
3. **Düzeltme mağaza turu gerektiriyor.** Bozuk bir çeviri, yanlış eşleşmiş bir hadis ya da
   30 kayıtlık dua havuzunu büyütmek — hepsi yeni sürüm demek.

## Tasarım kararı: seçimi merkezîleştir, yedeği yerelde bırak

Uç nokta **hangi** ayet/hadis/dua olduğunu söyler ve içeriği derlenmiş hâlde döndürür.
Uygulama günde bir çeker, önbelleğe alır; **istek başarısız olursa bugünkü yerel mantığa
düşer** (paketteki korpus + mevcut ayet servisi).

Paketteki korpüs küçültülmez. O artık "yedek" değil, çevrimdışı yolun kendisidir: paketi
kırpmak, ağı olmayan kullanıcı için sessiz bir gerileme olurdu. `fetchWithCache`'in
`stale-fallback` / `bundled` kaynak ayrımı ve çevrimdışı notu bu durumu zaten modelliyor.

## Uç nokta

```
GET /v1/daily/{date}?lang=tr|en
Authorization: Bearer <RAG_API_TOKEN>     # /v1/chat ile aynı
```

`lang=en` verildiğinde hadis ve dua yalnızca İngilizcesi olan kayıtlar arasından seçilir;
havuz `MIN_POOL`'un (30) altındaysa o kart **null** döner ve uygulama kartı hiç çizmez.
Uygulamayı İngilizce kullanan kişiye Türkçe metin gösterip altına "Turkish translation"
yazmak bir çözüm değil, özürdü. Ayet her dilde var (üç sürüm birlikte saklanıyor), o yüzden
hiç null dönmez.

`date`, **uygulamanın yerel takvim günü** (`YYYY-MM-DD`) — sunucu `now()` kullanmaz. Sunucu
okuyucunun saat dilimini bilemez ve gün, kullanıcının gece yarısında dönmelidir
(`siraj-mobile/src/utils/dayIndex.ts`). Tarih yoldadır ki cevap CDN'de önbelleklenebilsin.

`Cache-Control: public, max-age=21600` (6 saat). Bir tarihin içeriği normalde sabittir;
altı saat, küratör bir günü elle değiştirdiğinde değişikliğin aynı gün yayılmasına izin
verecek kadar kısadır. Uygulama zaten kendi tarafında gün boyu önbellekler.

### Cevap

Alanlar **uygulamanın kendi tiplerinin birebir aynısıdır** (`AyahContent`, `HadithContent`,
`DuaContent`). Böylece uzak yol ile çevrimdışı yol aynı şekli üretir ve uygulamada iki ayrı
model tutulmaz.

```jsonc
{
  "date": "2026-08-04",
  "ayah": {
    "globalNumber": 1961,
    "surahNumber": 16,
    "numberInSurah": 60,
    "surahNameArabic": "النحل",
    "surahNameEnglish": "An-Nahl",
    "arabic": "…",
    "translationTr": "…",
    "translationEn": "…",
    "url": "https://kuran.diyanet.gov.tr/mushaf/…/ayet-60/…"   // ek alan, kart kaynağı
  },
  "hadith": { "id": "hadeethenc:3779", "collection": "…", "collectionLabel": "…",
              "reference": "…", "narrator": "…", "grade": "Sahih Hadis",
              "text": "…", "textEn": "…", "narratorEn": "…", "referenceEn": "…",
              "gradeEn": "…" },
  "dua": { "id": "dua-…", "category": "…", "arabic": "…", "turkish": "…",
           "transliteration": "…", "source": "…", "english": "…",
           "categoryEn": "…", "sourceEn": "…" }
}
```

Eksik alanlar (`textEn`, `english` …) bugünkü gibi opsiyoneldir; uygulama zaten Türkçeye
düşüp "Türkçe meali" notunu gösteriyor.

### Hata

Uç nokta 5xx dönerse ya da ulaşılamazsa uygulama sessizce yerel mantığa düşer — kullanıcıya
hata gösterilmez, çünkü gösterilecek içerik zaten vardır. `404` kullanılmaz: kayıtlı bir gün
yoksa sunucu da deterministik rotasyona düşer (aşağıda).

## Seçim mantığı

```
daily_schedule tablosunda o tarih için satır var mı?
├── var  → küratörün seçtiği ayet/hadis/dua
└── yok  → deterministik rotasyon: days_since_epoch(date) % korpüs_boyu
```

İkinci dal, uygulamanın çevrimdışı yaptığının aynısıdır. Yani **her günü doldurmak zorunlu
değildir**; tablo yalnızca müdahale edilen günler için yazılır (Ramazan, Cuma, kandil, ya da
"bu ayet tek başına kötü okunuyor" denilen bir gün).

## Şema (`schema.sql`)

`migrate()` açılışta idempotent uyguladığı için tanım oraya girer.

```sql
CREATE TABLE IF NOT EXISTS daily_hadith (
    id      text PRIMARY KEY,      -- uygulamadaki HadithContent.id ile aynı
    ordinal int  NOT NULL,         -- rotasyon sırası (pakettekiyle aynı sıra)
    payload jsonb NOT NULL         -- HadithContent, olduğu gibi
);

CREATE TABLE IF NOT EXISTS daily_dua (
    id      text PRIMARY KEY,
    ordinal int  NOT NULL,
    payload jsonb NOT NULL         -- DuaContent, olduğu gibi
);

-- Ayet metni: üç sürüm de alquran.cloud'dan bir kez alınıp burada durur.
-- Türkçe meal olarak Diyanet **Vakfı** (tr.vakfi) korunuyor -- uygulamanın bugün
-- gösterdiği metin bu; değiştirmek görünen çeviriyi değiştirmek olurdu. Yani
-- chunks(source='meal') (Diyanet İşleri meali) burada kullanılmıyor; korpüsteki hâli
-- sohbetin kaynak listesi için duruyor.
--
-- Kazanç yine de gerçek: bugün her cihaz her gün üçüncü tarafa gidiyor, bundan sonra
-- sunucu bir kez gidiyor ve herkes bizden okuyor.
CREATE TABLE IF NOT EXISTS ayah_text (
    global_number  int PRIMARY KEY,
    arabic         text NOT NULL,
    translation_tr text NOT NULL,   -- tr.vakfi
    translation_en text,            -- en.asad
    surah_number   int  NOT NULL,
    number_in_surah int NOT NULL,
    surah_name_ar  text NOT NULL,
    surah_name_en  text NOT NULL,
    fetched_at     timestamptz NOT NULL DEFAULT now()
);

-- Yalnızca müdahale edilen günler. Boş bırakılan alan o tür için rotasyona düşer.
CREATE TABLE IF NOT EXISTS daily_schedule (
    date       date PRIMARY KEY,
    ayah_global int,
    hadith_id  text REFERENCES daily_hadith(id),
    dua_id     text REFERENCES daily_dua(id),
    note       text                -- neden seçildiği; yalnızca insan için
);
```

`daily_hadith` / `daily_dua` **`chunks` üzerinden karşılanamaz**: `chunks` bir arama
korpüsüdür — pencerelenmiş metin ve embedding. Oradaki `hadis` kaynağı *Hadislerle İslam*
cilt metnidir, kartın istediği kısa söz + ravi + derece değil; `dua` kaynağı da genel
pencereleme yolundan geçer. Kart korpüsü bugün yalnızca uygulamanın içinde vardır ve
tohumlanması gerekir.

## Tohumlama

Uygulamadaki `src/data/hadiths.json` ve `src/data/duas.json` doğrudan `payload` olarak
yazılır; `ordinal` dosyadaki sıradır, böylece rotasyon paketle **birebir aynı** günü seçer.

Ingest-şeklinde bir iştir: `siraj-backend/CLAUDE.md` gereği **yerel Mac'ten, SSH tüneli
üzerinden** çalışır, sunucuda değil.

## Uygulama tarafı

- Yeni servis, üçünü tek istekte çeker ve `fetchWithCache` ile gün boyu önbelleğe alır.
- Başarısızlıkta bugünkü yerel yol aynen çalışır; `source` alanı `stale-fallback` /
  `bundled` ayrımını korur, çevrimdışı notu bugünkü gibi görünür.
- **`CACHE_SCHEMA_VERSION` bump edilmez**: Türkçe meal Diyanet Vakfı olarak kalıyor, yani
  gösterilen metin aynı. Uç noktanın döndürdüğü ayet, bugün cihazın alquran.cloud'dan
  aldığının birebir aynısıdır — tek fark, isteği kimin yaptığı.
- `siraj-mobile/STORE.md` veri güvenliği bölümüne bir cümle: uygulama artık
  `siraj-api.ravey.app`'e sohbet sorusunun yanında bir de **tarih** gönderiyor.

## Sırayla

1. **Şema + tohumlama + uç nokta**, yalnızca deterministik rotasyonla. Bu adımda kullanıcıya
   görünen hiçbir şey değişmez; uzak yol ile yerel yol aynı kartı seçer. Doğrulaması da
   budur: aynı tarih için iki yol aynı `id`'yi vermeli.
2. **Uygulama**, uzak yolu kullanmaya başlar, hata hâlinde yerele düşer.
3. **Küratörlük**: `curation/schedule.json` + `python -m ingest.seed_schedule`. Doğrudan
   INSERT değil, depoya işlenmiş bir dosya: `daily_schedule` satırları üretim verisidir ve
   kodda görünmez; dosya olunca seçim aylar sonra diff'te okunabiliyor ve veritabanı
   yeniden kurulduğunda tek komutla geri geliyor.

   İlk parti: 5 Ağustos — 1 Kasım 2026 arası **39 gün**. Sıralı yürüyüşün o günlerde
   verdiği ayet günlük kart olarak kötü okunuyordu; ölçüt üç başlıkta ve her satırın
   notunda hangisi olduğu yazıyor (çıplak tehdit, bir önceki ayete bağlı parça, dönemin
   muhataplarına dönük polemik; ayrıca fıkhî hüküm parçası ve anlatı ortası). 90 günün
   39'u — yani bu bir istisna değil, yöntemin doğal sonucu.

   Yalnızca ayet sabitlendi; hadis ve dua o günlerde de rotasyonda. İkisinin korpüsü zaten
   küratörlükten geçmiş, sorun sıralı yürüyüşe özgü.

   **Sırada:** Ramazan, Cuma ve kandil geceleri. Bunlar hicrî takvim gerektiriyor ve
   Diyanet'in takvimi ile aritmetik takvimler bir gün kayabiliyor; tarihler resmî takvimle
   doğrulanmadan yazılmamalı.

## Dua havuzu (yapıldı)

30 kayıt ayda bir başa sarıyordu. `siraj-crawler/data/duas_aday.json` içindeki 95 adaydan
**kaynağı olan 28'i** alındı, Arapça metne göre tekilleştirilerek: havuz **58 dua**, yaklaşık
iki aylık dönüş.

Kalan 67 aday alınmadı: hiçbirinde kaynak yok ve Türkçeleri bozuk ("Rahmetten Kovulmuş
Şeytanın Şerriden Allah Sığınıyorum"). Dua kartı kaynağı ekranda gösteriyor ve PRODUCT.md
ilke 6 kaynağı olmayan içeriği doldurma amaçlı eklemeyi yasaklıyor.

Havuz **hem pakette hem veritabanında aynı** olmalı: aynı sıra, aynı rotasyon. Uzak yol ile
çevrimdışı yolun aynı günü aynı kartla karşılaması bunun üzerine kurulu.
