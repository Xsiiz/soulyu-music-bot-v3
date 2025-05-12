# soulyu-music-bot-v3
soulyu-music-bot-v3

## คุณสมบัติหลัก
* เล่นเพลงจาก YouTube (ชื่อ, URL, Playlist, Mix)
* ระบบคิวเพลง (เพิ่ม, ลบ, ดูคิว, ล้างคิว)
* ข้ามเพลง, หยุดเพลง, เล่นเพลงต่อ
* ปรับระดับเสียงเพลงอัตโนมัติ
* ออกจากห้องเสียงอัตโนมัติเมื่อไม่ได้ใช้งาน
* ... (เพิ่มตามฟังก์ชันจริง)

## ข้อกำหนดเบื้องต้น
* Python 3.8+
* FFMPEG (ต้องอยู่ใน PATH ของระบบ)
* Git

## การติดตั้ง
1.  Clone repository นี้:
    ```bash
    git clone [https://github.com/your-username/your-repository-name.git](https://github.com/your-username/your-repository-name.git)
    cd your-repository-name
    ```
2.  (แนะนำ) สร้างและเปิดใช้งาน Virtual Environment:
    ```bash
    python -m venv venv
    # Windows
    .\venv\Scripts\activate
    # macOS/Linux
    source venv/bin/activate
    ```
3.  ติดตั้ง Dependencies:
    ```bash
    pip install -r requirements.txt
    ```
4.  **ตั้งค่า FFMPEG:** ตรวจสอบให้แน่ใจว่า FFMPEG ได้รับการติดตั้งและเพิ่มเข้าสู่ PATH ของระบบของคุณแล้ว
5.  **ตั้งค่า Bot Token:**
    * คัดลอกไฟล์ `.env.example` ไปเป็น `.env`:
      ```bash
      # Windows
      copy .env.example .env
      # macOS/Linux
      cp .env.example .env
      ```
    * แก้ไขไฟล์ `.env` แล้วใส่ `DISCORD_BOT_TOKEN` ของคุณ:
      ```
      DISCORD_BOT_TOKEN=โทเคนบอทของคุณที่นี่
      ```

## การใช้งาน
ในการรันบอท:
```bash
python main.py
