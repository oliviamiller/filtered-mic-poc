#pragma once

#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>
#include <viam/sdk/common/audio.hpp>
#include <viam/sdk/components/audio_in.hpp>
#include <viam/sdk/config/resource.hpp>
#include <viam/sdk/resource/reconfigurable.hpp>
#include <fvad.h>
#include <vosk_api.h>

namespace voice_trigger {
namespace vsdk = ::viam::sdk;

class VoiceTriggerMic final : public vsdk::AudioIn, public vsdk::Reconfigurable {
public:
    VoiceTriggerMic(vsdk::Dependencies deps, vsdk::ResourceConfig cfg);
    ~VoiceTriggerMic() override;

    static std::vector<std::string> validate(vsdk::ResourceConfig cfg);

    // AudioIn interface
    void get_audio(std::string const& codec,
                   std::function<bool(vsdk::AudioIn::audio_chunk&& chunk)> const& chunk_handler,
                   double const& duration_seconds,
                   int64_t const& previous_timestamp,
                   const vsdk::ProtoStruct& extra) override;

    vsdk::audio_properties get_properties(const vsdk::ProtoStruct& extra) override;
    std::vector<vsdk::GeometryConfig> get_geometries(const vsdk::ProtoStruct& extra) override;
    vsdk::ProtoStruct do_command(const vsdk::ProtoStruct& command) override;

    void reconfigure(const vsdk::Dependencies& deps, const vsdk::ResourceConfig& cfg) override;

    static vsdk::Model model;

private:
    struct AudioBufferState {
        std::vector<vsdk::AudioIn::audio_chunk> chunk_buffer;
        std::vector<uint8_t> byte_buffer;
        bool is_speech_active = false;
        int silence_frames = 0;
        static constexpr int max_silence_frames = 30;  // ~1 second at 30ms frames
    };

    bool check_for_trigger(const std::vector<uint8_t>& audio_bytes, int sample_rate);

    // Configuration (protected by config_mu_)
    std::string source_microphone_;
    std::string trigger_word_;
    int vad_aggressiveness_;

    // Dependencies
    std::shared_ptr<vsdk::AudioIn> microphone_client_;

    // VAD and Speech Recognition
    Fvad* vad_ = nullptr;
    VoskModel* vosk_model_ = nullptr;

    // Mutex for thread safety
    std::mutex config_mu_;
};

}  // namespace voice_trigger
