import os
import subprocess
import glob
import time
import asyncio
import edge_tts
from datetime import datetime
from zoneinfo import ZoneInfo
from google import genai

TARGET_URL = "https://cdn-fr1-eu.lncoperations.ee/hls/cnbc_live/index.m3u8"

# 🛠️ อัด 10800 วินาที (3 ชม.) / ตัดท่อนละ 420 วินาที (7 นาที)
RECORD_DURATION = 10800
SEGMENT_DURATION = 420

# 🔑 ดึง Key จาก GitHub Secret อัตโนมัติ
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def record_stream(output_filename, duration):
    """บันทึกเสียงสดจาก CNBC เป็นไฟล์ .mp3"""
    print("🤖 เริ่มต้นทำงานระบบบันทึกเสียงอัตโนมัติ...")
    print(f"🎙️ กำลังบันทึกเสียงเป็นไฟล์ MP3 เป็นเวลา {duration} วินาที...")

    headers = (
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"
        "Referer: https://livenewschat.eu/\r\n"
    )

    cmd = [
        'ffmpeg', '-y',
        '-loglevel', 'error', # 🟢 ป้องกัน Log ล้น RAM ระหว่างอัด 3 ชั่วโมง
        '-headers', headers,
        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
        '-reconnect', '1',
        '-reconnect_streamed', '1',
        '-reconnect_delay_max', '5',
        '-i', TARGET_URL,
        '-t', str(duration),
        '-vn',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        output_filename
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ FFmpeg Error:\n{result.stderr}")
        return False

    return os.path.exists(output_filename) and os.path.getsize(output_filename) > 0

def split_audio(input_file, folder_name, date_prefix, segment_time=15):
    """ตัดแบ่งไฟล์เสียง .mp3"""
    print(f"\n✂️ กำลังตัดแบ่งไฟล์ '{input_file}' เป็นท่อนละ {segment_time} วินาที...")
    # บันทึกไฟล์ลงใน Folder ที่กำหนด
    output_pattern = f"{folder_name}/{date_prefix}_part_%03d.mp3"

    cmd = [
        'ffmpeg', '-y',
        '-loglevel', 'error', # 🟢 ป้องกัน Log ขยะ
        '-i', input_file,
        '-f', 'segment',
        '-segment_time', str(segment_time),
        '-c', 'copy',
        output_pattern
    ]
    subprocess.run(cmd, check=True)
    segments = sorted(glob.glob(f"{folder_name}/{date_prefix}_part_*.mp3"))
    print(f"🎉 ตัดไฟล์สำเร็จ! ได้ทั้งหมด {len(segments)} ไฟล์\n")
    return segments

def transcribe_and_translate(audio_path, max_retries=3):
    """ส่งไฟล์เสียงไปแปลไทยด้วย Gemini"""
    if not client:
        print("  ⚠️ ไม่พบ GEMINI_API_KEY ข้ามการแปลภาษา")
        return None

    print(f"  🤖 [1/3] กำลังส่งเสียงให้ Gemini ฟังและแปลไทย...")

    for attempt in range(1, max_retries + 1):
        audio_file = None
        try:
            audio_file = client.files.upload(file=audio_path)

            prompt = """
            คำสั่งสำคัญที่สุด: ผลลัพธ์ของคุณต้องเป็น "ภาษาไทยล้วน 100%" เท่านั้น
            1. ฟังเสียงพูดภาษาอังกฤษทั้งหมด แล้วแปลบทพูดทุกประโยคออกมาเป็นภาษาไทยโดยตรง
            2. ห้ามพิมพ์ภาษาอังกฤษต้นฉบับออกมาเด็ดขาด
            3. ห้ามทำรูปแบบประโยคภาษาอังกฤษสลับกับภาษาไทย (Bilingual)
            4. แปลถ่ายทอดเนื้อหาคำพูดและบทวิเคราะห์ให้ครบถ้วนทุกประโยคตั้งแต่ต้นจนจบ
            5. ไม่ต้องใส่ตัวเลขเวลา (Timestamp)
            6. ให้ส่งออกเฉพาะข้อความภาษาไทยที่อ่านได้อย่างต่อเนื่อง สละสลวย เท่านั้น
            7. ลบภาษาอังกฤษออก
            """

            # 🟢 เปลี่ยนไปใช้ Model ล่าสุดที่มีอยู่จริง
            response = client.models.generate_content(
                model='gemini-3.5-flash-lite', 
                contents=[audio_file, prompt]
            )

            return response.text

        except Exception as e:
            print(f"  ⚠️ ครั้งที่ {attempt} พบปัญหา ({e})")
            if attempt < max_retries:
                time.sleep(attempt * 5)
            else:
                return None
        finally:
            # 🟢 ใส่ finally เพื่อการันตีว่าไฟล์จะถูกลบออกจาก Storage เสมอ ป้องกันโควต้าเต็ม
            if audio_file:
                try:
                    client.files.delete(name=audio_file.name)
                except:
                    pass

async def text_to_speech_thai(text, output_audio_path):
    """สร้างไฟล์เสียงอ่านข่าวไทย"""
    if not text or not text.strip():
        print("  ⚠️ ไม่มีข้อความให้สร้างเสียง")
        return None

    print(f"  🗣️ [3/3] กำลังสร้างไฟล์เสียงอ่านข่าวไทย: {output_audio_path}...")
    try:
        voice = "th-TH-PremwadeeNeural"
        tts = edge_tts.Communicate(text, voice)
        await tts.save(output_audio_path)
        print(f"  ✅ บันทึกเสียงพากย์ไทยสำเร็จ!")
        return output_audio_path
    except Exception as e:
        print(f"  ❌ สังเคราะห์เสียงอ่านข่าวล้มเหลว: {e}")
        return None

def process_single_file(seg_path, current_idx, total_files):
    """ส่งคืนที่อยู่ไฟล์เสียง TTS ถ้าทำสำเร็จ"""
    print(f"==================================================")
    print(f"🔄 กำลังประมวลผลไฟล์ [{current_idx}/{total_files}]: {os.path.basename(seg_path)}")
    print(f"==================================================")

    th_text = transcribe_and_translate(seg_path)
    if not th_text:
        return None

    txt_filename = seg_path.replace(".mp3", "_แปลไทย.txt")
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(th_text)
    print(f"  💾 [2/3] บันทึกคำแปลข้อความ: {txt_filename}")

    tts_filename = seg_path.replace(".mp3", "_อ่านข่าวไทย.mp3")
    result_tts = asyncio.run(text_to_speech_thai(th_text, tts_filename))
    
    print(f"🎉 เสร็จสิ้นขั้นตอนของไฟล์ [{current_idx}/{total_files}]\n")
    return result_tts

def concatenate_audio(tts_files, output_filename, folder_name):
    """นำไฟล์ mp3 ที่อ่านข่าวไทยทั้งหมดมาต่อกันเป็นไฟล์เดียว"""
    if not tts_files:
        print("⚠️ ไม่มีไฟล์เสียงสำหรับนำมารวมกัน")
        return

    print(f"\n🔗 กำลังรวมไฟล์เสียงพากย์ไทยทั้งหมด {len(tts_files)} ไฟล์...")
    list_file = f"{folder_name}/concat_list.txt"
    
    # สร้างไฟล์ list สำหรับ ffmpeg
    with open(list_file, "w", encoding="utf-8") as f:
        for audio_file in tts_files:
            # ใช้ relative path ให้ถูกต้องสำหรับ ffmpeg
            safe_path = audio_file.replace("\\", "/")
            f.write(f"file '{os.path.basename(safe_path)}'\n") # อ้างอิงไฟล์ในโฟลเดอร์เดียวกัน

    cmd = [
        'ffmpeg', '-y',
        '-loglevel', 'error',
        '-f', 'concat',
        '-safe', '0',
        '-i', list_file,
        '-c', 'copy',
        output_filename
    ]

    # รันคำสั่ง ffmpeg โดยกำหนด working directory เป็นโฟลเดอร์นั้น
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=folder_name)
    
    if result.returncode == 0 and os.path.exists(output_filename):
        print(f"✅ รวมไฟล์เสียงเสร็จสมบูรณ์! ไฟล์ปลายทาง: {output_filename}")
    else:
        print(f"❌ การรวมไฟล์ล้มเหลว:\n{result.stderr}")

    # ลบไฟล์ list ทิ้งหลังใช้งานเสร็จ
    if os.path.exists(list_file):
        os.remove(list_file)

if __name__ == "__main__":
    th_time = datetime.now(ZoneInfo("Asia/Bangkok"))
    date_str = th_time.strftime('%Y%m%d_%H%M%S')
    
    # 🟢 สร้างชื่อโฟลเดอร์จาก [ชื่อไฟล์ YML]_[เวลา-นาที]
    yml_name = os.getenv("GITHUB_WORKFLOW", "recording_job").replace(" ", "_")
    time_min_str = th_time.strftime('%H-%M')
    folder_name = f"./{yml_name}_{time_min_str}"
    
    # สร้างโฟลเดอร์
    os.makedirs(folder_name, exist_ok=True)
    print(f"📁 สร้างโฟลเดอร์สำหรับทำงาน: {folder_name}")

    # กำหนดที่อยู่ไฟล์หลักให้อยู่ในโฟลเดอร์
    main_file = f"{folder_name}/raw_cnbc_{date_str}.mp3"

    success = record_stream(main_file, RECORD_DURATION)

    if success:
        print(f"✅ บันทึกไฟล์หลักสำเร็จ: {main_file}")
        # ส่ง folder_name เข้าไปในฟังก์ชัน split_audio
        segment_files = split_audio(main_file, folder_name, date_str, SEGMENT_DURATION)
        total_segments = len(segment_files)

        # 🟢 ตัวแปรสำหรับเก็บรายชื่อไฟล์เสียงที่สร้างสำเร็จ
        successful_tts_files = []

        for idx, seg in enumerate(segment_files, start=1):
            generated_tts_file = process_single_file(seg, idx, total_segments)
            
            if generated_tts_file:
                successful_tts_files.append(generated_tts_file)
                
            # 🟢 พัก 5 วินาที ลดความเสี่ยงการโดนแบน (Rate Limit) จาก API
            time.sleep(5) 

        # 🟢 เมื่อประมวลผลเสร็จทุกไฟล์ ให้นำมาต่อรวมกัน
        if successful_tts_files:
            final_output_file = f"{folder_name}/final_thai_news_{date_str}.mp3"
            concatenate_audio(successful_tts_files, final_output_file, folder_name)

        print("✨ ประมวลผลและรวมไฟล์เสร็จสิ้นเรียบร้อยแล้ว!")
    else:
        print("❌ การบันทึกเสียงล้มเหลว")
