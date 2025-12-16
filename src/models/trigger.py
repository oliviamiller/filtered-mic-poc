from typing import (Any, ClassVar, Dict, Final, List, Mapping, Optional,
                    Sequence, Tuple, cast)

import asyncio
import json
import os
import time
import logging
import speech_recognition as sr
from vosk import Model as VoskModel, KaldiRecognizer
from typing_extensions import Self
from viam.components.audio_in import *
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import Geometry, ResourceName
from viam.resource.base import ResourceBase
from viam.resource.easy_resource import EasyResource
from viam.resource.types import Model, ModelFamily
from viam.utils import ValueTypes, struct_to_dict
from viam.streams import Stream, StreamWithIterator
from hearken import Listener
from hearken.vad import WebRTCVAD
from .viamaudiosource import ViamAudioInSource




class Trigger(AudioIn, EasyResource):
    # To enable debug-level logging, either run viam-server with the --debug option,
    # or configure your resource/machine to display debug logs.
    MODEL: ClassVar[Model] = Model(ModelFamily("viam", "filter-mic"), "trigger")

    @classmethod
    def new(
        cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]
    ) -> Self:
        """This method creates a new instance of this AudioIn component.
        The default implementation sets the name from the `config` parameter.

        Args:
            config (ComponentConfig): The configuration for this resource
            dependencies (Mapping[ResourceName, ResourceBase]): The dependencies (both required and optional)

        Returns:
            Self: The resource
        """
        instance = super().new(config, dependencies)
        instance.logger.info("=== Trigger component initialization started ===")

        attrs = struct_to_dict(config.attributes)
        microphone = str(attrs.get("source_microphone", ""))
        instance.trigger_word = str(attrs.get("trigger_word", "")).lower()
        model_path = str(attrs.get("vosk_model_path", "~/vosk-model-small-en-us-0.15"))
        vad_aggressiveness = int(attrs.get("vad_aggressiveness", 3))  # 0-3, higher = less sensitive

        instance.logger.info(f"Config: microphone='{microphone}', trigger_word='{instance.trigger_word}'")

        # Set up VOSK model with automatic download fallback
        model_path = os.path.expanduser(model_path)
        instance.logger.info(f"Expanded model path: {model_path}")

        if not os.path.exists(model_path):
            instance.logger.info(f"Vosk model not found at {model_path}, attempting to download...")
            if not instance._download_model():
                instance.logger.error("Failed to download Vosk model")
                raise RuntimeError(f"Vosk model not found at {model_path} and download failed")

        try:
            instance.logger.info("Loading Vosk model...")
            instance.vosk_model = VoskModel(model_path)
            instance.logger.info(f"Successfully loaded Vosk model from {model_path}")
        except Exception as e:
            instance.logger.error(f"Failed to load Vosk model: {e}")
            raise RuntimeError(f"Failed to load Vosk model from {model_path}: {e}")

        instance.vosk_recognizer = None  # Will be created per audio segment

        # Initialize trigger state
        instance.logger.info("Initializing trigger state...")
        instance.buffer_duration_ns = int(attrs.get("buffer_seconds", 5)) * 1_000_000_000  # Default 5 seconds
        instance.last_chunk_timestamp_ns = 0  # Track most recent audio timestamp
        instance.trigger_detected = False  # Flag for trigger word detection
        instance.trigger_timestamp_ns = 0  # Timestamp when trigger was detected
        instance.trigger_segment_duration_ns = 0  # Duration of trigger segment
        instance.logger.info(f"Trigger state initialized (buffer: {instance.buffer_duration_ns / 1_000_000_000}s)")

        if microphone != "":
            instance.logger.info(f"Setting up microphone client: {microphone}")
            mic = dependencies[AudioIn.get_resource_name(microphone)]
            instance.microphone_client = cast(AudioIn, mic)
            instance.logger.info("Microphone client set up successfully")

            # Get the running event loop
            instance.logger.info("Getting event loop...")
            try:
                instance.main_loop = asyncio.get_running_loop()
                instance.logger.info("Using running event loop")
            except RuntimeError:
                instance.main_loop = asyncio.get_event_loop()
                instance.logger.info("Created new event loop")

            # Set up hearken listener with ViamAudioInSource
            instance.logger.info("Creating ViamAudioInSource...")
            instance.audio_source = ViamAudioInSource(
                microphone_client=instance.microphone_client,
                logger=instance.logger
            )
            instance.logger.info("ViamAudioInSource created successfully")

            # Set up VAD
            instance.logger.info(f"Creating listener VAD with aggressiveness={vad_aggressiveness}...")
            vad = WebRTCVAD(aggressiveness=vad_aggressiveness)
            instance.logger.info("Listener VAD created successfully")

            # Set up listener with callbacks and event loop
            instance.logger.info("Creating hearken Listener...")
            instance.listener = Listener(
                source=instance.audio_source,
                vad=vad,
                on_error=lambda err: instance.logger.error(f"hearken listener error: {err}"),
                on_speech=lambda segment: instance.listen_callback(
                    sr.AudioData(
                        segment.audio_data,
                        segment.sample_rate,
                        segment.sample_width,
                    )
                ),
                event_loop=instance.main_loop,
            )
            instance.listener_started = False
            instance.logger.info("Listener created successfully")
        else:
            instance.logger.info("No microphone configured, skipping audio setup")
            instance.microphone_client = None
            instance.audio_source = None
            instance.listener = None
            instance.main_loop = None
            instance.listener_started = False

        instance.logger.info("=== Trigger component initialization completed ===")

        hearken_logger = logging.getLogger("hearken")
        hearken_logger.setLevel(logging.DEBUG)
        return instance

    @classmethod
    def validate_config(
        cls, config: ComponentConfig
    ) -> Tuple[Sequence[str], Sequence[str]]:
        """This method allows you to validate the configuration object received from the machine,
        as well as to return any required dependencies or optional dependencies based on that `config`.

        Args:
            config (ComponentConfig): The configuration for this resource

        Returns:
            Tuple[Sequence[str], Sequence[str]]: A tuple where the
                first element is a list of required dependencies and the
                second element is a list of optional dependencies
        """
        deps = []
        attrs = struct_to_dict(config.attributes)
        microphone = str(attrs.get("source_microphone", ""))
        if microphone != "":
            deps.append(microphone)
        return deps, []

    def listen_callback(self, audio_data: sr.AudioData):
        """Handle speech segments detected by hearken listener.

        Args:
            audio_data: Speech audio data from the listener
        """
        # Calculate segment duration
        segment_duration_seconds = len(audio_data.frame_data) / (audio_data.sample_rate * audio_data.sample_width)

        # Ignore very short segments (likely just noise clicks)
        if segment_duration_seconds < 0.5:  # Less than 500ms
            self.logger.debug(f"Ignoring short segment: {segment_duration_seconds:.2f}s")
            return

        self.logger.info(f"Speech detected: {len(audio_data.frame_data)} bytes ({segment_duration_seconds:.1f}s)")

        # Create a new recognizer for this audio segment
        recognizer = KaldiRecognizer(self.vosk_model, audio_data.sample_rate)

        # Process the audio data
        recognizer.AcceptWaveform(audio_data.get_raw_data())

        # Get the final result (forces Vosk to finalize)
        result = json.loads(recognizer.FinalResult())
        recognized_text = result.get("text", "").lower()

        if recognized_text:
            self.logger.info(f"Recognized: {recognized_text}")

            # Check if trigger word is present
            if self.trigger_word and self.trigger_word in recognized_text:
                self.logger.info(f"Trigger word '{self.trigger_word}' detected!")

                # Calculate segment duration from audio data
                segment_duration_seconds = len(audio_data.frame_data) / (audio_data.sample_rate * audio_data.sample_width)
                segment_duration_ns = int(segment_duration_seconds * 1_000_000_000)
                self.logger.info(f"Segment duration: {segment_duration_seconds:.2f}s ({segment_duration_ns}ns)")

                self.on_trigger_detected(recognized_text, segment_duration_ns)
        else:
            self.logger.debug("No text recognized in this segment")

    def on_trigger_detected(self, recognized_text: str, segment_duration_ns: int):
        """Called when the trigger word is detected.

        Args:
            recognized_text: The full recognized text containing the trigger word
            segment_duration_ns: Duration of the speech segment in nanoseconds
        """
        self.logger.info(f"TRIGGER DETECTED! Text: {recognized_text}")

        # Store the segment duration for historical fetch
        self.trigger_segment_duration_ns = segment_duration_ns

        # Use the most recent audio chunk timestamp (not system time)
        self.trigger_timestamp_ns = self.last_chunk_timestamp_ns
        self.trigger_detected = True
        self.logger.info(f"Trigger at audio timestamp {self.trigger_timestamp_ns}, will fetch {segment_duration_ns / 1_000_000_000:.2f}s of historical audio")

    async def get_audio(
            self,
            codec: str,
            duration_seconds: float,
            previous_timestamp_ns: int,
            *,
            timeout: Optional[float] = None,
            **kwargs
    ) -> Stream[AudioResponse]:
        self.logger.info("filter: get_audio called")

        # Start the listener on first call
        if self.listener is not None and not self.listener_started:
            self.logger.info("Starting hearken listener")
            self.listener.start()
            self.listener_started = True

        # Define the async generator for streaming audio
        async def audio_generator():
            if self.microphone_client is not None:
                # Start monitoring stream to track timestamps
                self.logger.info("Starting to monitor audio stream for timestamps")
                monitor_stream = await self.microphone_client.get_audio(codec, duration_seconds, previous_timestamp_ns)

                first_chunk = True
                async for chunk in monitor_stream:
                    # Track the most recent timestamp
                    if chunk.audio.end_timestamp_nanoseconds > 0:
                        self.last_chunk_timestamp_ns = chunk.audio.end_timestamp_nanoseconds

                    # Yield first chunk to unblock Go client
                    if first_chunk:
                        yield chunk
                        first_chunk = False

                    # Check for trigger signal
                    if self.trigger_detected:
                        self.logger.info(f"Fetching historical audio from {self.trigger_segment_duration_ns / 1_000_000_000}s ago")

                        # Request historical audio from microphone's buffer
                        historical_start = self.trigger_timestamp_ns - self.trigger_segment_duration_ns
                        buffer_duration_seconds = self.trigger_segment_duration_ns / 1_000_000_000
                        historical_stream = await self.microphone_client.get_audio(
                            codec,
                            duration_seconds=buffer_duration_seconds,
                            previous_timestamp_ns=historical_start
                        )

                        # Yield all historical audio (will stop after duration_seconds)
                        async for historical_chunk in historical_stream:
                            yield historical_chunk

                        self.trigger_detected = False
                        self.logger.info("Finished replaying historical audio")

        # Wrap the generator in StreamWithIterator and return it
        return StreamWithIterator(audio_generator())

    async def close(self):
        """Clean up resources when component is closed."""
        if self.listener is not None and self.listener_started:
            self.logger.info("Stopping hearken listener")
            self.listener.stop()
            self.listener_started = False

        if self.audio_source is not None:
            self.audio_source.close()

    async def do_command(
        self,
        command: Mapping[str, ValueTypes],
        *,
        timeout: Optional[float] = None,
        **kwargs
    ) -> Mapping[str, ValueTypes]:
        self.logger.error("`do_command` is not implemented")
        raise NotImplementedError()

    async def get_geometries(
        self, *, extra: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None
    ) -> Sequence[Geometry]:
        self.logger.error("`get_geometries` is not implemented")
        raise NotImplementedError()


    def _download_model(self) -> bool:
        """Download Vosk model automatically"""
        try:
            import urllib.request
            import zipfile
            import ssl
            import certifi

            model_name = "vosk-model-small-en-us-0.15"
            model_url = f"https://alphacephei.com/vosk/models/{model_name}.zip"
            model_path = os.path.expanduser(f"~/{model_name}")
            zip_path = os.path.expanduser(f"~/{model_name}.zip")

            self.logger.debug(f"Downloading Vosk model from {model_url}")

            # Download with SSL context using certifi certificates
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(model_url, context=ssl_context) as response:
                with open(zip_path, 'wb') as out_file:
                    out_file.write(response.read())

            # Extract
            self.logger.debug("Extracting Vosk model...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(os.path.expanduser("~/"))

            # Clean up
            os.remove(zip_path)

            if os.path.exists(model_path):
                self.logger.debug(f"Vosk model downloaded to {model_path}")
                return True
            else:
                self.logger.error("Failed to extract Vosk model")
                return False

        except Exception as e:
            self.logger.error(f"Failed to download Vosk model: {e}")
            return False

    async def get_properties(self, *, timeout: Optional[float] = None, **kwargs) -> AudioIn.Properties:
        self.logger.debug(f"in get rpoeprties")

