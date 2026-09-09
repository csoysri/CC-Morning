import os
import subprocess
import glob
import time
import asyncio
import edge_tts
import shutil  # เพิ่ม import shutil สำหรับการย้าย/คัดลอกไฟล์
from datetime import datetime
from zoneinfo import ZoneInfo
from google import genai

TARGET_URL = "https://cdn-fr1-eu.lncoperations.ee/hls/cnbc_live/index.m3u8" 

# 🛠️ ตั้งเวลา: อัด 3 ชั่วโมง (10800 วินาที) / ตัดท่อนละ 7 นาที (420 วินาที)
RECORD_DURATION = 14400  
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
    
    # แก้ไขให้นำคำว่า part_ ขึ้นต้นชื่อไฟล์
    output_pattern = os.path.join(folder_name, f"part_{date_prefix}_%03d.mp3")

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
    
    # อัปเดตแพทเทิร์นค้นหาไฟล์ให้ตรงกับชื่อไฟล์ใหม่
    segments = sorted(glob.glob(os.path.join(folder_name, f"part_{date_prefix}_*.mp3")))
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
                model='gemini-3.5-flash-lite',
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

# --- 🛠️ ฟังก์ชันใหม่สำหรับต่อไฟล์เสียงแบบอเนกประสงค์ ---
def concat_audio_files(input_files, output_filename):
    """ฟังก์ชันย่อยสำหรับรวมไฟล์เสียงด้วย FFmpeg"""
    if len(input_files) == 1:
        shutil.copy(input_files[0], output_filename)
        return True

    cmd = ['ffmpeg', '-y']
    for f in input_files:
        cmd.extend(['-i', os.path.abspath(f)])

    n = len(input_files)
    filter_inputs = "".join([f"[{i}:a]" for i in range(n)])
    filter_str = f"{filter_inputs}concat=n={n}:v=0:a=1[outa]"

    cmd.extend([
        '-filter_complex', filter_str,
        '-map', '[outa]',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        '-ar', '44100',
        '-ac', '2',
        '-map_metadata', '-1',
        output_filename
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and os.path.exists(output_filename)

def merge_and_cleanup_tts(tts_files, final_output_filename, folder_name):
    """
    รวมไฟล์เสียงอ่านข่าวโดยแบ่งทำทีละ 10 ไฟล์ 
    เพื่อลดภาระของ FFmpeg และป้องกันบั๊กเมื่อรวมไฟล์จำนวนมากพร้อมกัน
    """
    print(f"==================================================")
    print(f"🔗 กำลังรวมไฟล์เสียงทั้งหมด {len(tts_files)} ไฟล์ (แบ่งทำทีละ 10 ไฟล์)...")

    if not tts_files:
        print("⚠️ ไม่มีไฟล์เสียงสำหรับรวม")
        return

    # กรณีมีไฟล์เดียว
    if len(tts_files) == 1:
        shutil.move(tts_files[0], final_output_filename)
        print(f"✅ มีเพียงไฟล์เดียว บันทึกสำเร็จ: {final_output_filename}")
        return

    batch_size = 10
    intermediate_files = []

    # 1. แบ่งกลุ่มไฟล์ทีละ 10 ไฟล์
    for i in range(0, len(tts_files), batch_size):
        batch = tts_files[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        temp_output = os.path.join(folder_name, f"temp_batch_{batch_num}.mp3")

        print(f"  ⏳ กำลังรวมกลุ่มที่ {batch_num} ({len(batch)} ไฟล์) -> {os.path.basename(temp_output)} ...")
        success = concat_audio_files(batch, temp_output)

        if success:
            intermediate_files.append(temp_output)
            # ลบไฟล์ย่อยเฉพาะใน batch ที่รวมสำเร็จแล้ว
            for f in batch:
                try:
                    os.remove(f)
                except Exception as e:
                    pass
        else:
            print(f"  ❌ รวมกลุ่มที่ {batch_num} ล้มเหลว!")

    # 2. นำไฟล์ชั่วคราว (temp_batch_X) มารวมกันเป็นไฟล์สุดท้าย
    if not intermediate_files:
        print("❌ ไม่สามารถสร้างไฟล์ชั่วคราวได้เลย ยกเลิกการรวมไฟล์")
        return

    print(f"==================================================")
    print(f"🔗 กำลังรวมไฟล์กลุ่มย่อยทั้งหมด {len(intermediate_files)} ไฟล์ เป็นไฟล์สุดท้าย...")
    
    if len(intermediate_files) == 1:
        # ถ้ามีแค่ batch เดียว (ไฟล์ต้นทาง < 10) ก็แค่เปลี่ยนชื่อ
        shutil.move(intermediate_files[0], final_output_filename)
        print(f"✅ รวมไฟล์สำเร็จสมบูรณ์: {final_output_filename}")
    else:
        # ถ้าน้อยกว่าหรือเท่ากับ 10 batch รวมกันได้เลย
        final_success = concat_audio_files(intermediate_files, final_output_filename)
        
        if final_success:
            print(f"✅ รวมไฟล์สำเร็จสมบูรณ์: {final_output_filename}")
            # ลบไฟล์ temp_batch ทิ้ง
            for f in intermediate_files:
                try:
                    os.remove(f)
                    print(f"  🗑️ ลบไฟล์กลุ่มย่อย: {os.path.basename(f)}")
                except:
                    pass
        else:
            print("❌ การรวมไฟล์ขั้นสุดท้ายล้มเหลว")

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
