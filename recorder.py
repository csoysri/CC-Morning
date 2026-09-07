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

# 🛠️ ตั้งเวลา: อัด 3 ชั่วโมง (10800 วินาที) / ตัดท่อนละ 7 นาที (420 วินาที)
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

def split_audio(input_file, date_prefix, folder_name, segment_time=420):
    """ตัดแบ่งไฟล์เสียง .mp3 พร้อมจัดเรียง timestamp รอยต่อให้สะอาด"""
    print(f"\n✂️ กำลังตัดแบ่งไฟล์ '{input_file}' เป็นท่อนละ {segment_time} วินาที...")
    
    output_pattern = os.path.join(folder_name, f"{date_prefix}_part_%03d.mp3")

    # เพิ่ม -avoid_negative_ts make_zero เพื่อป้องกันปัญหา Timestamp ติดลบ/สะดุดรอยต่อ
    cmd = [
        'ffmpeg', '-y',
        '-i', input_file,
        '-f', 'segment',
        '-segment_time', str(segment_time),
        '-avoid_negative_ts', 'make_zero',
        '-c', 'copy',
        output_pattern
    ]
    subprocess.run(cmd, check=True)
    
    segments = sorted(glob.glob(os.path.join(folder_name, f"{date_prefix}_part_*.mp3")))
    print(f"🎉 ตัดไฟล์สำเร็จ! ได้ทั้งหมด {len(segments)} ไฟล์\n")
    return segments

def transcribe_and_translate(audio_path, max_retries=3):
    """ส่งไฟล์เสียงไปแปลไทยด้วย Gemini พร้อมควบคุมอาการหลอน/พูดซ้ำ"""
    if not client:
        print("  ⚠️ ไม่พบ GEMINI_API_KEY ข้ามการแปลภาษา")
        return None

    print(f"  🤖 [1/3] กำลังส่งเสียงให้ Gemini ฟังและแปลไทย...")

    for attempt in range(1, max_retries + 1):
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
            7. กฎเหล็กป้องกันอาการวนลูป: หากไฟล์เสียงช่วงใดมีเฉพาะเสียงดนตรี ดนตรีคั่นรายการ หรือเป็นความเงียบโดยไม่มีเสียงคนพูด ให้ข้ามไป ห้ามแต่งเรื่อง ห้ามเดาข้อความ และห้ามทวนประโยคเดิมซ้ำโดยเด็ดขาด
            8. หากทั้งไฟล์ไม่มีเสียงพูดเลย ให้ตอบกลับมาเพียงสั้นๆ ว่า "ไม่มีเสียงบรรยายข่าว"
            """

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[audio_file, prompt]
            )

            client.files.delete(name=audio_file.name)
            text_result = response.text.strip() if response.text else ""
            
            # กรองท่อนที่ไม่มีเสียงพูดออก ไม่ต้องส่งไปสังเคราะห์เสียงอ่าน
            if "ไม่มีเสียงบรรยายข่าว" in text_result:
                return None
                
            return text_result

        except Exception as e:
            print(f"  ⚠️ ครั้งที่ {attempt} พบปัญหา ({e})")
            if attempt < max_retries:
                time.sleep(attempt * 5)
            else:
                return None

async def text_to_speech_thai(text, output_audio_path):
    """สร้างไฟล์เสียงอ่านข่าวไทย"""
    print(f"  🗣️ [3/3] กำลังสร้างไฟล์เสียงอ่านข่าวไทย: {output_audio_path}...")
    try:
        voice = "th-TH-PremwadeeNeural"
        tts = edge_tts.Communicate(text, voice)
        await tts.save(output_audio_path)
        print(f"  ✅ บันทึกเสียงพากย์ไทยสำเร็จ!")
    except Exception as e:
        print(f"  ❌ สังเคราะห์เสียงอ่านข่าวล้มเหลว: {e}")

def process_single_file(seg_path, current_idx, total_files):
    print(f"==================================================")
    print(f"🔄 กำลังประมวลผลไฟล์ [{current_idx}/{total_files}]: {os.path.basename(seg_path)}")
    print(f"==================================================")

    th_text = transcribe_and_translate(seg_path)
    if not th_text:
        print(f"  ⏭️ ข้ามการสร้างเสียงสำหรับไฟล์ {os.path.basename(seg_path)} (ไม่มีเสียงพูดหรือแปลไม่สำเร็จ)")
        return None

    txt_filename = seg_path.replace(".mp3", "_แปลไทย.txt")
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(th_text)
    print(f"  💾 [2/3] บันทึกคำแปลข้อความ: {txt_filename}")

    tts_filename = seg_path.replace(".mp3", "_อ่านข่าวไทย.mp3")
    asyncio.run(text_to_speech_thai(th_text, tts_filename))
    print(f"🎉 เสร็จสิ้นขั้นตอนของไฟล์ [{current_idx}/{total_files}]\n")
    
    return tts_filename

def merge_and_cleanup_tts(tts_files, output_filename, folder_name):
    """
    รวมไฟล์เสียงอ่านข่าวทั้งหมดด้วย Filter Complex Concat
    แก้ปัญหาเวลาเพี้ยน และเสียงวนซ้ำตรงรอยต่อไฟล์อย่างสมบูรณ์
    """
    print(f"==================================================")
    print(f"🔗 กำลังรวมไฟล์เสียงอ่านข่าวทั้งหมด {len(tts_files)} ไฟล์ (ระบบไร้รอยต่อ)...")

    if not tts_files:
        print("⚠️ ไม่มีไฟล์เสียงสำหรับรวม")
        return

    # กรณีมีไฟล์เดียว ให้ย้าย/เปลี่ยนชื่อได้ทันที
    if len(tts_files) == 1:
        shutil.move(tts_files[0], output_filename)
        print(f"✅ มีเพียงไฟล์เดียว บันทึกสำเร็จ: {output_filename}")
        return

    cmd = ['ffmpeg', '-y']

    # 1. ป้อน input เข้าทีละไฟล์
    for f in tts_files:
        cmd.extend(['-i', os.path.abspath(f)])

    # 2. ผูก Filter Concat เข้าด้วยกัน (Decode เป็น PCM ก่อนต่อ)
    n = len(tts_files)
    filter_inputs = "".join([f"[{i}:a]" for i in range(n)])
    filter_str = f"{filter_inputs}concat=n={n}:v=0:a=1[outa]"

    cmd.extend([
        '-filter_complex', filter_str,
        '-map', '[outa]',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        '-ar', '44100',          # บังคับ Sample Rate เท่ากัน ป้องกันเวลาเพี้ยน
        '-ac', '2',              # บังคับ Stereo เท่ากันทุกไฟล์
        '-map_metadata', '-1',   # ลบ Metadata เก่าที่ฝังตรงรอยต่อของแต่ละพาร์ท
        output_filename
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0 and os.path.exists(output_filename):
        print(f"✅ รวมไฟล์เสียงสำเร็จสมบูรณ์: {output_filename}")
        
        # ลบเฉพาะไฟล์ย่อยที่นำมารวมแล้ว
        for tts in tts_files:
            try:
                os.remove(tts)
                print(f"  🗑️ ลบไฟล์ย่อย: {tts}")
            except Exception as e:
                print(f"  ⚠️ ไม่สามารถลบไฟล์ {tts} ได้: {e}")
    else:
        print(f"❌ การรวมไฟล์ล้มเหลว: {result.stderr}")

if __name__ == "__main__":
    th_time = datetime.now(ZoneInfo("Asia/Bangkok"))
    date_str = th_time.strftime('%Y%m%d_%H%M%S')
    
    # 📁 1. ดึงชื่อไฟล์ yml จาก Github Actions (หากไม่มีจะใช้ค่า Default เป็น "CNBC_Workflow")
    yml_name = os.getenv("GITHUB_WORKFLOW", "CNBC_Workflow")
    yml_name = yml_name.replace(" ", "_")
    
    # 📁 2. นำชื่อ yml มาต่อด้วย เวลา-นาที (HH-MM)
    folder_time = th_time.strftime('%H-%M') 
    folder_name = f"{yml_name}_{folder_time}"
    
    # 📁 3. สร้างโฟลเดอร์
    os.makedirs(folder_name, exist_ok=True)
    print(f"📁 สร้างโฟลเดอร์สำหรับเก็บผลลัพธ์: {folder_name}\n")

    main_file = os.path.join(folder_name, f"raw_cnbc_{date_str}.mp3")

    success = record_stream(main_file, RECORD_DURATION)

    if success:
        print(f"✅ บันทึกไฟล์หลักสำเร็จ: {main_file}")
        segment_files = split_audio(main_file, date_str, folder_name, SEGMENT_DURATION)
        total_segments = len(segment_files)
        
        generated_tts_files = []

        for idx, seg in enumerate(segment_files, start=1):
            tts_file = process_single_file(seg, idx, total_segments)
            if tts_file and os.path.exists(tts_file):
                generated_tts_files.append(tts_file)
            time.sleep(2)

        print("✨ ประมวลผลและแปลครบทุกไฟล์เรียบร้อยแล้ว!")
        
        # ดำเนินการรวมไฟล์เสียงอ่านข่าวทั้งหมดและลบไฟล์ย่อย
        if generated_tts_files:
            final_audio = os.path.join(folder_name, f"final_thai_news_{date_str}.mp3")
            merge_and_cleanup_tts(generated_tts_files, final_audio, folder_name)
            
    else:
        print("❌ การบันทึกเสียงล้มเหลว")
