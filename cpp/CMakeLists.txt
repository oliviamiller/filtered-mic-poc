cmake_minimum_required(VERSION 3.10)
set(CMAKE_PROJECT_VERSION 0.0.1)
set(CMAKE_CXX_STANDARD 17)

project(voice-trigger-mic
    DESCRIPTION "Viam module for voice-activated trigger microphone"
    HOMEPAGE_URL https://github.com/viam-modules/filter-mic
    LANGUAGES CXX
)

add_executable(voice-trigger-module
    main.cpp
    voice_trigger_mic.cpp
)

find_package(viam-cpp-sdk REQUIRED)
find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)

# Find libfvad
pkg_check_modules(FVAD REQUIRED fvad)

# Find Vosk
find_library(VOSK_LIBRARY NAMES vosk REQUIRED)
find_path(VOSK_INCLUDE_DIR NAMES vosk_api.h REQUIRED)

target_link_libraries(voice-trigger-module
    PRIVATE Threads::Threads
    PRIVATE viam-cpp-sdk::viamsdk
    PRIVATE ${FVAD_LIBRARIES}
    PRIVATE ${VOSK_LIBRARY}
)

target_include_directories(voice-trigger-module
    PRIVATE ${FVAD_INCLUDE_DIRS}
    PRIVATE ${VOSK_INCLUDE_DIR}
)

target_compile_options(voice-trigger-module
    PRIVATE ${FVAD_CFLAGS_OTHER}
)

install(TARGETS voice-trigger-module DESTINATION .)
install(FILES meta.json DESTINATION .)
