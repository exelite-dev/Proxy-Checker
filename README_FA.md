# چکر پروکسی تلگرام 

نسخه فارسی مستندات پروژه.

[🇬🇧 English README](README.md)

## معرفی

این پروژه یک ابزار بررسی پراکسی تلگرام با رابط گرافیکی دسکتاپ است که می‌تواند:

- پراکسی‌ها را دستی Paste کند و تست بگیرد
- از فایل `proxies.txt` بخواند
- از لینک‌های Raw گیت‌هاب پراکسی جدید بگیرد
- با فاصله زمانی مشخص Auto-Fetch انجام دهد
- فقط پراکسی‌های سالم را با پینگ در `working.txt` ذخیره کند

## قابلیت‌ها

- UI گرافیکی با تم بنفش (`customtkinter`)
- پشتیبانی از `MTProto`، `HTTP` و `SOCKS5`
- اسکن Async با Worker Pool (مناسب لیست‌های بزرگ)
- محدودسازی اتصال همزمان با `TCPConnector(limit ~= concurrency * 2)`
- حالت `Strict Mode` برای دقت بالاتر (کندتر)
- جدول نتایج + قابلیت کپی پراکسی

## نصب

```bash
pip install -r requirements.txt
```

## اجرا

### ویندوز (رابط گرافیکی)

```powershell
.\run.bat
```

### اجرای CLI

```bash
python telegram_proxy_checker.py -i proxies.txt -o working.txt -t 5 -c 300
```

### CLI با دقت بالاتر

```bash
python telegram_proxy_checker.py -i proxies.txt -o working.txt -t 5 -c 300 --strict
```

## فرمت ورودی

نمونه‌های قابل قبول:

```text
tg://proxy?server=1.2.3.4&port=443&secret=dd...
https://t.me/proxy?server=1.2.3.4&port=443&secret=dd...
1.2.3.4:443:mtproto:dd...
1.2.3.4:1080:socks5
1.2.3.4:8080:http
```

برای نمونه آماده: `proxies.sample.txt`

## خروجی

خروجی سالم‌ها در `working.txt`:

```text
tg://proxy?server=1.2.3.4&port=443&secret=dd... | 275.4 ms
```


- `proxy_gui.py`: رابط گرافیکی اصلی
- `telegram_proxy_checker.py`: موتور اسکن CLI
- `run.bat`: اجرای سریع ویندوز
- `requirements.txt`: وابستگی‌ها
- `proxies.sample.txt`: نمونه ورودی
- `README.md`: مستندات انگلیسی
- در نسخه ZIP فعلی، پوشه `legacy/` وجود ندارد.
