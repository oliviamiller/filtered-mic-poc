# Voice Trigger Microphone - C++ Implementation

C++ implementation of the voice-activated trigger microphone component using WebRTC VAD and Vosk speech recognition.

## Features

- **WebRTC VAD**: Lightweight voice activity detection for low CPU usage
- **Vosk Speech Recognition**: Offline trigger word detection
- **Smart Buffering**: Only buffers audio during active speech (no long silence periods)
- **Viam SDK Integration**: Full integration with Viam robotics platform

## Architecture

This implementation mirrors the Python version (`src/models/trigger.py`) but uses the Viam C++ SDK patterns from the audio-poc reference implementation.

### Key Differences from Python Version

1. **Callback-based**: Uses `chunk_handler` callback instead of generator/iterator pattern
2. **RAII**: Proper C++ resource management for VAD and Vosk objects
3. **Thread-safe**: Uses mutex for configuration protection
4. **Reconfigurable**: Implements reconfiguration interface for dynamic updates

## Building

### Dependencies

- Viam C++ SDK
- WebRTC Audio Processing library
- Vosk API
- CMake 3.10+
- C++17 compiler

### Build Instructions

```bash
cd cpp
mkdir build && cd build
cmake ..
cmake --build .
```

## Configuration

```json
{
  "name": "voice-trigger",
  "model": "viam:filtered-audio:voice-trigger-mic",
  "type": "audio_input",
  "attributes": {
    "source_microphone": "mic",
    "trigger_word": "robot",
    "vosk_model_path": "~/vosk-model-small-en-us-0.15",
    "vad_aggressiveness": 3
  }
}
```

### Attributes

- `source_microphone`: Name of the underlying microphone component
- `trigger_word`: Word or phrase to detect (case-insensitive)
- `vosk_model_path`: Path to Vosk model directory
- `vad_aggressiveness`: VAD sensitivity (0-3, higher = less sensitive)

## How It Works

1. **Continuous Monitoring**: Streams audio from source microphone
2. **VAD Processing**: Each chunk is analyzed frame-by-frame (30ms frames)
3. **Speech Detection**: When speech detected, starts buffering chunks
4. **Silence Detection**: After ~1 second of silence, processes buffered audio
5. **Trigger Recognition**: Runs Vosk on speech segment to find trigger word
6. **Chunk Delivery**: If trigger found, yields all buffered chunks to client

## Performance

- **Low CPU**: WebRTC VAD is very lightweight (~1% CPU)
- **No Constant Processing**: Vosk only runs on speech segments, not continuously
- **Smart Buffering**: No memory waste on long silence periods

## Comparison to Python Version

| Aspect | Python | C++ |
|--------|--------|-----|
| Pattern | Generator/Iterator | Callback |
| Memory | Automatic GC | Manual RAII |
| Performance | ~Good | ~Excellent |
| Threading | GIL limitations | Native threads |
| SDK API | `StreamWithIterator` | `chunk_handler` |

Both implementations use identical logic for VAD and trigger detection.
