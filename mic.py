import pyaudio
import numpy as np
import time

p = pyaudio.PyAudio()
CHUNKS = 1024
RATE = 16000

# Find all input devices
devices = []
for i in range(p.get_device_count()):
    dev = p.get_device_info_by_index(i)
    if dev['maxInputChannels'] > 0:
        devices.append((i, dev['name']))

print("\n🔍 Scanning for active audio inputs... Speak or clap to see which one jumps!")
print("Press Ctrl+C to stop.\n")

# Open streams for all devices
streams = {}
for idx, name in devices:
    try:
        streams[idx] = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            input_device_index=idx,
            frames_per_buffer=CHUNKS
        )
        print(f"✅ Listening to Index {idx}: {name[:40]}")
    except Exception:
        # Skip devices that fail to open at 16kHz
        pass

try:
    while True:
        output_str = ""
        for idx, stream in streams.items():
            try:
                # Read data without blocking
                data = stream.read(CHUNKS, exception_on_overflow=False)
                audio_np = np.frombuffer(data, dtype=np.int16)
                
                # Calculate the volume level (root mean square)
                volume = np.sqrt(np.mean(audio_np**2))
                
                # Create a simple visual bar
                bar = "#" * int(volume / 200)
                output_str += f"Index {idx} Vol: {volume:5.0f} {bar[:20]}\t"
            except:
                pass
        
        # Print on the same line
        print(output_str, end="\r")
        time.sleep(0.1)

except KeyboardInterrupt:
    print("\n🛑 Stopping scan...")
    for s in streams.values():
        s.stop_stream()
        s.close()
    p.terminate()