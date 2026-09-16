import os
import subprocess
import glob
import time
import asyncio
import edge_tts
import shutil
import re
import tempfile
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
    
    output_pattern = os.path.join(folder_name, f"part_{date_prefix}_%03d.mp3")

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

# --- 🛠️ ฟังก์ชันรวมไฟล์ที่ปลอดภัย 100% ด้วย Concat Demuxer + Re-encode ---
def concat_audio_files_safe(input_files, output_filename, temp_work_dir):
    """
    รวมไฟล์เสียงโดยใช้ Concat Demuxer (ไฟล์ลิสต์ .txt)
    ป้องกันปัญหา Command line ยาวเกินไป และ Re-encode เพื่อให้ Timestamp ลื่นไหลไม่กระตุก
    """
    if not input_files:
        return False

    if len(input_files) == 1:
        shutil.copy(input_files[0], output_filename)
        return True

    # สร้างไฟล์รายชื่อชั่วคราว
    list_path = os.path.join(temp_work_dir, "concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for file_path in input_files:
            # แปลง Path ให้ FFmpeg เข้าใจแน่นอนบน Windows (ใช้ slash '/')
            clean_path = os.path.abspath(file_path).replace("\\", "/")
            f.write(f"file '{clean_path}'\n")

    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', list_path,
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        '-ar', '44100',
        '-ac', '2',
        output_filename
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    
    # ลบไฟล์ list ชั่วคราวทิ้ง
    if os.path.exists(list_path):
        os.remove(list_path)

    if result.returncode != 0:
        print(f"❌ FFmpeg Concat Error:\n{result.stderr}")
        return False

    return os.path.exists(output_filename) and os.path.getsize(output_filename) > 0

def merge_and_cleanup_tts(tts_files, final_output_filename, folder_name):
    """รวมไฟล์เสียงอ่านข่าวทั้งหมด แล้วลบไฟล์ย่อยเฉพาะที่รวมเสร็จแล้ว"""
    print(f"==================================================")
    print(f"🔗 กำลังรวมไฟล์เสียงอ่านข่าวทั้งหมด {len(tts_files)} ไฟล์...")

    if not tts_files:
        print("⚠️ ไม่มีไฟล์เสียงสำหรับรวม")
        return

    # ให้เวลา Windows คลาย Lock ไฟล์
    time.sleep(1)

    success = concat_audio_files_safe(tts_files, final_output_filename, folder_name)

    if success:
        print(f"✅ รวมไฟล์เสียงอ่านข่าวสมบูรณ์ 100%: {final_output_filename}")
        # ลบเฉพาะไฟล์ _อ่านข่าวไทย.mp3 รายท่อนทิ้ง เพื่อไม่ให้รกโฟลเดอร์
        for f in tts_files:
            try:
                os.remove(f)
            except Exception:
                pass
    else:
        print("❌ การรวมไฟล์ขั้นสุดท้ายล้มเหลว (ไฟล์ต้นฉบับยังคงอยู่ครบถ้วน)")

if __name__ == "__main__":
    th_time = datetime.now(ZoneInfo("Asia/Bangkok"))
    
    # รูปแบบวันที่และเวลาตามโครงสร้างภาพ
    date_folder = th_time.strftime('%Y-%m-%d')  # เช่น 2026-09-16
    time_folder = th_time.strftime('%H-%M')     # เช่น 09-12
    date_str = th_time.strftime('%Y%m%d_%H%M%S')

    # 1. จัดการชื่อโฟลเดอร์ย่อย และลบอักขระพิเศษด้วย Regex (ป้องกันปัญหาเครื่องหมาย /, \ แตกโฟลเดอร์)
    raw_workflow_name = os.getenv("GITHUB_WORKFLOW", "CC-Morning")
    clean_workflow_name = re.sub(r'[\\/*?:"<>|]', '', raw_workflow_name).strip().replace(" ", "_")
    if not clean_workflow_name:
        clean_workflow_name = "CC-Morning"

    # 2. กำหนด Path ปลายทางตัวจริง (Google Drive หรือ Local)
    gdrive_root = os.getenv("GDRIVE_PATH", r"G:\My Drive")
    if os.path.exists(gdrive_root):
        target_dir = os.path.join(gdrive_root, "CNBC", clean_workflow_name, date_folder, time_folder)
    else:
        target_dir = os.path.join("CNBC", clean_workflow_name, date_folder, time_folder)

    # 3. 🛡️ จุดสำคัญที่สุด: สร้าง Local Work Directory ในเครื่องก่อนเสมอ
    # เพื่อป้องกันไม่ให้ FFmpeg อ่าน/เขียนสะดุดบน Google Drive เสมือน
    local_work_dir = os.path.join(tempfile.gettempdir(), f"CNBC_WORK_{date_str}")
    os.makedirs(local_work_dir, exist_ok=True)
    print(f"📁 พื้นที่ประมวลผลชั่วคราว (Local Fast I/O): {local_work_dir}")
    print(f"🎯 โฟลเดอร์เป้าหมายปลายทาง: {target_dir}\n")

    main_file = os.path.join(local_work_dir, f"raw_cnbc_{date_str}.mp3")

    success = record_stream(main_file, RECORD_DURATION)

    if success:
        print(f"✅ บันทึกไฟล์หลักสำเร็จ: {main_file}")
        segment_files = split_audio(main_file, date_str, local_work_dir, SEGMENT_DURATION)
        total_segments = len(segment_files)
        
        generated_tts_files = []

        for idx, seg in enumerate(segment_files, start=1):
            tts_file = process_single_file(seg, idx, total_segments)
            if tts_file and os.path.exists(tts_file):
                generated_tts_files.append(tts_file)
            time.sleep(1)

        print("✨ ประมวลผลและแปลครบทุกไฟล์เรียบร้อยแล้ว!")
        
        # รวมไฟล์เสียงอ่านข่าวทั้งหมดในเครื่องก่อน
        if generated_tts_files:
            final_audio = os.path.join(local_work_dir, f"final_thai_news_{date_str}.mp3")
            merge_and_cleanup_tts(generated_tts_files, final_audio, local_work_dir)

        # 4. 🚚 ย้ายไฟล์ทั้งหมดที่เสร็จสมบูรณ์ 100% เข้า Google Drive ปลายทาง
        print(f"\n🚚 กำลังย้ายไฟล์ทั้งหมดไปยังเป้าหมาย: {target_dir} ...")
        os.makedirs(target_dir, exist_ok=True)
        
        for item in os.listdir(local_work_dir):
            src_item = os.path.join(local_work_dir, item)
            dst_item = os.path.join(target_dir, item)
            try:
                if os.path.exists(dst_item):
                    if os.path.isdir(dst_item):
                        shutil.rmtree(dst_item)
                    else:
                        os.remove(dst_item)
                shutil.move(src_item, dst_item)
            except Exception as e:
                # กรณีติด Permission ให้ fallback เป็น copy แล้ว delete
                shutil.copy2(src_item, dst_item)
                os.remove(src_item)
                
        # ลบโฟลเดอร์ Temp
        shutil.rmtree(local_work_dir, ignore_errors=True)
        print(f"🎉 เสร็จสิ้นทุกขั้นตอน! ไฟล์ทุกไฟล์ถูกจัดเก็บใน Google Drive เรียบร้อยแล้ว")
            
    else:
        print("❌ การบันทึกเสียงล้มเหลว")
        shutil.rmtree(local_work_dir, ignore_errors=True)
