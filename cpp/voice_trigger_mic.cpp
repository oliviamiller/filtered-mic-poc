#include "voice_trigger_mic.hpp"
#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <cstring>

namespace voice_trigger {

vsdk::Model VoiceTriggerMic::model("viam", "filtered-audio", "voice-trigger-mic");

VoiceTriggerMic::VoiceTriggerMic(vsdk::Dependencies deps, vsdk::ResourceConfig cfg) {
    VIAM_SDK_LOG(info) << "=== Voice Trigger Mic Init ===";

    // Parse configuration
    auto attrs = cfg.attributes();
    source_microphone_ = attrs->get<std::string>("source_microphone").value_or("");
    trigger_word_ = attrs->get<std::string>("trigger_word").value_or("");
    std::string model_path = attrs->get<std::string>("vosk_model_path").value_or("~/vosk-model-small-en-us-0.15");
    vad_aggressiveness_ = attrs->get<int>("vad_aggressiveness").value_or(3);

    // Convert trigger word to lowercase
    std::transform(trigger_word_.begin(), trigger_word_.end(),
                   trigger_word_.begin(), ::tolower);

    VIAM_SDK_LOG(info) << "Trigger word: '" << trigger_word_ << "'";
    VIAM_SDK_LOG(info) << "VAD aggressiveness: " << vad_aggressiveness_;

    // Initialize libfvad
    vad_ = fvad_new();
    if (!vad_) {
        throw std::runtime_error("Failed to create fvad instance");
    }
    if (fvad_set_mode(vad_, vad_aggressiveness_) != 0) {
        fvad_free(vad_);
        throw std::runtime_error("Failed to set VAD mode");
    }
    if (fvad_set_sample_rate(vad_, 16000) != 0) {
        fvad_free(vad_);
        throw std::runtime_error("Failed to set VAD sample rate");
    }
    VIAM_SDK_LOG(info) << "libfvad initialized";

    // Expand home directory in model path
    if (model_path[0] == '~') {
        const char* home = getenv("HOME");
        if (home) {
            model_path = std::string(home) + model_path.substr(1);
        }
    }

    // Load Vosk model
    vosk_model_ = vosk_model_new(model_path.c_str());
    if (!vosk_model_) {
        fvad_free(vad_);
        throw std::runtime_error("Vosk model not found at " + model_path);
    }
    VIAM_SDK_LOG(info) << "Vosk model loaded";

    // Get microphone dependency
    if (!source_microphone_.empty()) {
        try {
            microphone_client_ = deps.get_resource<vsdk::AudioIn>(source_microphone_);
            VIAM_SDK_LOG(info) << "Microphone: " << source_microphone_;
        } catch (const std::exception& e) {
            fvad_free(vad_);
            vosk_model_free(vosk_model_);
            throw std::runtime_error("Failed to get microphone '" + source_microphone_ + "': " + e.what());
        }
    }

    VIAM_SDK_LOG(info) << "=== Init Complete ===";
}

VoiceTriggerMic::~VoiceTriggerMic() {
    if (vad_) {
        fvad_free(vad_);
    }
    if (vosk_model_) {
        vosk_model_free(vosk_model_);
    }
    VIAM_SDK_LOG(info) << "Closing voice trigger component";
}

std::vector<std::string> VoiceTriggerMic::validate(vsdk::ResourceConfig cfg) {
    std::vector<std::string> deps;
    auto attrs = cfg.attributes();

    auto microphone = attrs->get<std::string>("source_microphone");
    if (microphone.has_value() && !microphone->empty()) {
        deps.push_back(*microphone);
    }

    return deps;
}

bool VoiceTriggerMic::check_for_trigger(const std::vector<uint8_t>& audio_bytes,
                                         int sample_rate) {
    try {
        VoskRecognizer* recognizer = vosk_recognizer_new(vosk_model_, sample_rate);
        if (!recognizer) {
            VIAM_SDK_LOG(error) << "Failed to create Vosk recognizer";
            return false;
        }

        // Process audio
        vosk_recognizer_accept_waveform(recognizer,
                                        reinterpret_cast<const char*>(audio_bytes.data()),
                                        audio_bytes.size());
        const char* result_json = vosk_recognizer_final_result(recognizer);

        // Parse JSON result (simple string search for "text" field)
        std::string result(result_json);
        size_t text_pos = result.find("\"text\" : \"");
        if (text_pos != std::string::npos) {
            text_pos += 10;  // Skip past "text" : "
            size_t end_pos = result.find("\"", text_pos);
            if (end_pos != std::string::npos) {
                std::string text = result.substr(text_pos, end_pos - text_pos);

                // Convert to lowercase
                std::transform(text.begin(), text.end(), text.begin(), ::tolower);

                if (!text.empty()) {
                    VIAM_SDK_LOG(debug) << "Recognized: " << text;
                }

                // Check for trigger word
                if (!trigger_word_.empty() && text.find(trigger_word_) != std::string::npos) {
                    VIAM_SDK_LOG(info) << "TRIGGER WORD '" << trigger_word_ << "' DETECTED!";
                    vosk_recognizer_free(recognizer);
                    return true;
                }
            }
        }

        vosk_recognizer_free(recognizer);
        return false;

    } catch (const std::exception& e) {
        VIAM_SDK_LOG(error) << "Vosk error: " << e.what();
        return false;
    }
}

void VoiceTriggerMic::get_audio(
    std::string const& codec,
    std::function<bool(vsdk::AudioIn::audio_chunk&& chunk)> const& chunk_handler,
    double const& duration_seconds,
    int64_t const& previous_timestamp,
    const vsdk::ProtoStruct& extra) {

    if (!microphone_client_) {
        VIAM_SDK_LOG(error) << "No microphone configured";
        return;
    }

    VIAM_SDK_LOG(info) << "Starting trigger detection with VAD...";

    AudioBufferState state;
    constexpr int frame_size = 960;  // 30ms at 16kHz = 480 samples * 2 bytes = 960 bytes

    // Get audio from microphone with callback
    microphone_client_->get_audio(
        codec,
        [this, &state, &chunk_handler, frame_size](vsdk::AudioIn::audio_chunk&& chunk) -> bool {
            auto& audio_data = chunk.data;

            if (audio_data.empty()) {
                return true;  // Continue streaming
            }

            // Check PCM16 alignment
            if (audio_data.size() % 2 != 0) {
                VIAM_SDK_LOG(warning) << "Misaligned audio chunk detected: " << audio_data.size() << " bytes (odd length)";
            }

            bool should_process = false;

            // Process audio in VAD-compatible frames
            for (size_t i = 0; i < audio_data.size(); i += frame_size) {
                if (i + frame_size > audio_data.size()) {
                    continue;  // Skip incomplete frames
                }

                // Check if frame contains speech
                int is_speech = fvad_process(
                    vad_,
                    reinterpret_cast<const int16_t*>(&audio_data[i]),
                    frame_size / 2);  // length in samples

                if (is_speech == 1) {
                    if (!state.is_speech_active) {
                        VIAM_SDK_LOG(debug) << "Speech started";
                        state.is_speech_active = true;
                    }
                    state.silence_frames = 0;
                } else {
                    if (state.is_speech_active) {
                        state.silence_frames++;

                        if (state.silence_frames >= state.max_silence_frames) {
                            VIAM_SDK_LOG(debug) << "Speech ended (" << state.silence_frames << " silent frames)";
                            should_process = true;
                            break;
                        }
                    }
                }
            }

            // Only buffer during active speech
            if (state.is_speech_active) {
                state.chunk_buffer.push_back(chunk);
                state.byte_buffer.insert(state.byte_buffer.end(), audio_data.begin(), audio_data.end());
            }

            // If speech ended, check for trigger
            if (should_process) {
                VIAM_SDK_LOG(debug) << "Checking " << state.byte_buffer.size() << " bytes for trigger";

                if (check_for_trigger(state.byte_buffer, 16000)) {
                    VIAM_SDK_LOG(info) << "TRIGGER! Yielding " << state.chunk_buffer.size()
                                       << " chunks (" << state.byte_buffer.size() << " bytes)";

                    // Yield buffered chunks
                    for (auto& buffered_chunk : state.chunk_buffer) {
                        if (!chunk_handler(std::move(buffered_chunk))) {
                            return false;  // Client cancelled
                        }
                    }

                    VIAM_SDK_LOG(info) << "Ready for next trigger";
                } else {
                    VIAM_SDK_LOG(debug) << "No trigger found";
                }

                // Clear buffers
                state.chunk_buffer.clear();
                state.byte_buffer.clear();
                state.is_speech_active = false;
                state.silence_frames = 0;
            }

            // Safety: prevent buffer overflow
            if (state.byte_buffer.size() > 500000) {  // ~15 seconds max
                VIAM_SDK_LOG(warning) << "Buffer too large, force checking";

                if (check_for_trigger(state.byte_buffer, 16000)) {
                    VIAM_SDK_LOG(info) << "TRIGGER! Yielding " << state.chunk_buffer.size() << " chunks";

                    for (auto& buffered_chunk : state.chunk_buffer) {
                        if (!chunk_handler(std::move(buffered_chunk))) {
                            return false;
                        }
                    }
                }

                state.chunk_buffer.clear();
                state.byte_buffer.clear();
                state.is_speech_active = false;
                state.silence_frames = 0;
            }

            return true;  // Continue streaming
        },
        0,  // duration_seconds = 0 for continuous
        0,  // previous_timestamp = 0 for from start
        extra);
}

vsdk::audio_properties VoiceTriggerMic::get_properties(const vsdk::ProtoStruct& extra) {
    VIAM_SDK_LOG(debug) << "get_properties called";

    if (microphone_client_) {
        return microphone_client_->get_properties(extra);
    }

    return vsdk::audio_properties{16000, 1};
}

vsdk::ProtoStruct VoiceTriggerMic::do_command(const vsdk::ProtoStruct& command) {
    throw std::runtime_error("do_command not implemented");
}

std::vector<vsdk::GeometryConfig> VoiceTriggerMic::get_geometries(const vsdk::ProtoStruct& extra) {
    return {};
}

void VoiceTriggerMic::reconfigure(const vsdk::Dependencies& deps, const vsdk::ResourceConfig& cfg) {
    std::lock_guard<std::mutex> lock(config_mu_);
    VIAM_SDK_LOG(info) << "Reconfiguring voice trigger mic";
    // Could update configuration here if needed
}

}  // namespace voice_trigger
