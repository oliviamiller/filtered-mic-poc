import os
import re

from conan import ConanFile
from conan.tools.cmake import CMake, CMakeDeps, CMakeToolchain, cmake_layout
from conan.tools.files import load

class VoiceTriggerMic(ConanFile):
    name = "voice-trigger-mic"

    license = "Apache-2.0"
    url = "https://github.com/viam-modules/filter-mic"
    package_type = "application"
    settings = "os", "compiler", "build_type", "arch"

    options = {
        "shared": [True, False]
    }
    default_options = {
        "shared": True
    }

    exports_sources = "CMakeLists.txt", "src/*", "*.cpp", "*.hpp", "meta.json"

    def set_version(self):
        content = load(self, "CMakeLists.txt")
        self.version = re.search("set\(CMAKE_PROJECT_VERSION (.+)\)", content).group(1).strip()

    def configure(self):
        if not self.options.shared:
            self.options["*"].shared = False

    def requirements(self):
        # Match audio-poc version
        self.requires("viam-cpp-sdk/0.21.0")

    def build_requirements(self):
        # System dependencies (installed via brew/apt)
        # libfvad and vosk need to be installed separately
        pass

    def generate(self):
        tc = CMakeToolchain(self)
        tc.generate()
        CMakeDeps(self).generate()

    def build(self):
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def layout(self):
        cmake_layout(self, src_folder=".")

    def package(self):
        cmake = CMake(self)
        cmake.install()
