#!/usr/bin/env python3
"""
Step 1: Send every video in an S3 bucket through Amazon Bedrock Data Automation.
Produces, for each video, a transcript with speaker labels and timestamps,
chapter summaries, and a whole-video summary. Saves them to the same bucket
under transcripts/.

Run in AWS CloudShell (region: us-east-2):
    python3 1_process_videos.py YOUR-BUCKET-NAME
"""
import json, sys, time
import boto3

REGION = "us-east-2"
PROJECT_NAME = "documentary-zoom-videos"
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi")

if len(sys.argv) < 2:
    sys.exit("Usage: python3 1_process_videos.py YOUR-BUCKET-NAME")
BUCKET = sys.argv[1]

s3 = boto3.client("s3", region_name=REGION)
bda = boto3.client("bedrock-data-automation", region_name=REGION)
bda_rt = boto3.client("bedrock-data-automation-runtime", region_name=REGION)
account = boto3.client("sts").get_caller_identity()["Account"]
PROFILE_ARN = f"arn:aws:bedrock:{REGION}:{account}:data-automation-profile/us.data-automation-v1"


def get_or_create_project():
    for p in bda.list_data_automation_projects().get("projects", []):
        if p["projectName"] == PROJECT_NAME:
            return p["projectArn"]
    resp = bda.create_data_automation_project(
        projectName=PROJECT_NAME,
        projectDescription="Transcripts + chapters for documentary Zoom recordings",
        projectStage="LIVE",
        standardOutputConfiguration={
            "video": {
                "extraction": {
                    "category": {"state": "ENABLED", "types": ["TRANSCRIPT", "TEXT_DETECTION"]},
                    "boundingBox": {"state": "DISABLED"},
                },
                "generativeField": {"state": "ENABLED", "types": ["VIDEO_SUMMARY", "CHAPTER_SUMMARY"]},
            }
        },
    )
    print("Created project. Waiting 20s for it to become ready...")
    time.sleep(20)
    return resp["projectArn"]


def list_videos():
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        for o in page.get("Contents", []):
            k = o["Key"]
            if k.lower().endswith(VIDEO_EXT) and not k.startswith(("bda-output/", "transcripts/", "reports/")):
                keys.append(k)
    return keys


def already_done(stem):
    try:
        s3.head_object(Bucket=BUCKET, Key=f"transcripts/{stem}.txt")
        return True
    except Exception:
        return False


def read_json(uri):
    b, k = uri.replace("s3://", "").split("/", 1)
    return json.loads(s3.get_object(Bucket=b, Key=k)["Body"].read())


def ms_to_clock(ms):
    s = int(ms) // 1000
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


def build_transcript(result):
    """Turn BDA's result.json into a readable text file."""
    lines = []
    video = result.get("video", {})
    if video.get("summary"):
        lines += ["=== OVERALL SUMMARY (auto-generated) ===", video["summary"], ""]
    chapters = result.get("chapters") or []
    if chapters:
        lines.append("=== CHAPTERS ===")
        for i, ch in enumerate(chapters):
            lines.append(f"[{ch.get('start_timecode_smpte','?')} - {ch.get('end_timecode_smpte','?')}] Chapter {i+1}: {ch.get('summary','')}")
        lines += ["", "=== TRANSCRIPT (timestamps are hh:mm:ss; speakers are auto-labeled) ==="]
        for ch in chapters:
            for seg in ch.get("audio_segments", []):
                if seg.get("type") not in (None, "TRANSCRIPT"):
                    continue
                spk = (seg.get("speaker") or {}).get("speaker_label", "?")
                lines.append(f"[{ms_to_clock(seg.get('start_timestamp_millis', 0))}] {spk}: {seg.get('text','').strip()}")
    elif result.get("audio_segments"):
        lines.append("=== TRANSCRIPT (timestamps are hh:mm:ss; audio-only file, no speaker labels) ===")
        for seg in result["audio_segments"]:
            if seg.get("type") not in (None, "TRANSCRIPT"):
                continue
            spk = (seg.get("speaker") or {}).get("speaker_label")
            prefix = f"{spk}: " if spk else ""
            lines.append(f"[{ms_to_clock(seg.get('start_timestamp_millis', 0))}] {prefix}{seg.get('text','').strip()}")
    else:
        rep = video.get("transcript", {}).get("representation", {})
        lines += ["=== TRANSCRIPT ===", rep.get("text", "(no transcript found)")]
    return "\n".join(lines)


project_arn = get_or_create_project()
videos = list_videos()
print(f"Found {len(videos)} video(s) in s3://{BUCKET}")

jobs = {}
for key in videos:
    stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if already_done(stem):
        print(f"skip (already done): {key}")
        continue
    resp = bda_rt.invoke_data_automation_async(
        inputConfiguration={"s3Uri": f"s3://{BUCKET}/{key}"},
        outputConfiguration={"s3Uri": f"s3://{BUCKET}/bda-output/{stem}/"},
        dataAutomationConfiguration={"dataAutomationProjectArn": project_arn, "stage": "LIVE"},
        dataAutomationProfileArn=PROFILE_ARN,
    )
    jobs[resp["invocationArn"]] = (key, stem)
    print(f"submitted: {key}")

print(f"\n{len(jobs)} job(s) running. Checking every 60s (a 1-hour video takes roughly 10-20 min)...")
while jobs:
    time.sleep(60)
    for arn in list(jobs):
        key, stem = jobs[arn]
        st = bda_rt.get_data_automation_status(invocationArn=arn)
        status = st["status"]
        if status in ("Created", "InProgress"):
            continue
        del jobs[arn]
        if status != "Success":
            print(f"FAILED {key}: {status} {st.get('errorMessage','')}")
            continue
        meta = read_json(st["outputConfiguration"]["s3Uri"])
        result_uri = meta["output_metadata"][0]["segment_metadata"][0]["standard_output_path"]
        result = read_json(result_uri)
        s3.put_object(Bucket=BUCKET, Key=f"transcripts/{stem}.json", Body=json.dumps(result).encode())
        s3.put_object(Bucket=BUCKET, Key=f"transcripts/{stem}.txt", Body=build_transcript(result).encode())
        print(f"DONE {key} -> s3://{BUCKET}/transcripts/{stem}.txt   ({len(jobs)} still running)")

print("\nAll done. Now run:  python3 2_analyze.py", BUCKET)
