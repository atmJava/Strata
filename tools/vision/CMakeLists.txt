# tools/vision - strata-vision for the Linux CUDA sm_75 server.
#
# One executable contains llama.cpp's CPU and CUDA backends.  ``--gpu`` uses CUDA; without it the same
# executable reads on the CPU.  The server uses that as its automatic fallback when CUDA cannot start.
cmake_minimum_required(VERSION 3.24)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR "strata-vision in this distribution supports Linux only")
endif()
if(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64)$")
  message(FATAL_ERROR "strata-vision in this distribution supports x86_64 only")
endif()
if(DEFINED CMAKE_CUDA_ARCHITECTURES AND NOT "${CMAKE_CUDA_ARCHITECTURES}" STREQUAL "75")
  message(FATAL_ERROR "strata-vision supports CUDA sm_75 only; CMAKE_CUDA_ARCHITECTURES is '${CMAKE_CUDA_ARCHITECTURES}'")
endif()
set(CMAKE_CUDA_ARCHITECTURES 75 CACHE STRING "The only supported CUDA architecture" FORCE)

project(strata_vision LANGUAGES C CXX CUDA)

set(LLAMA_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/llama.cpp" CACHE PATH "llama.cpp checkout")
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_APP OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_MTMD ON CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)

set(GGML_NATIVE ON CACHE BOOL "" FORCE)
set(GGML_OPENMP ON CACHE BOOL "" FORCE)
set(GGML_BACKEND_DL OFF CACHE BOOL "" FORCE)
set(GGML_CUDA ON CACHE BOOL "" FORCE)
set(GGML_HIP OFF CACHE BOOL "" FORCE)
set(GGML_SYCL OFF CACHE BOOL "" FORCE)
set(GGML_VULKAN OFF CACHE BOOL "" FORCE)
set(GGML_METAL OFF CACHE BOOL "" FORCE)

add_subdirectory(${LLAMA_DIR} llama EXCLUDE_FROM_ALL)

add_executable(strata-vision strata_vision.cpp)
target_link_libraries(strata-vision PRIVATE mtmd llama)
target_compile_features(strata-vision PRIVATE cxx_std_17)
target_compile_definitions(strata-vision PRIVATE STRATA_VISION_CUDA=1)
set_target_properties(strata-vision PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin
  BUILD_WITH_INSTALL_RPATH ON
  INSTALL_RPATH "$ORIGIN"
)
target_link_options(strata-vision PRIVATE -Wl,--enable-new-dtags)

# Opens a GGUF with tensor types llama.cpp does not know (for example a Gyro quantization's TQK6 / TQK7)
# for its vocabulary only.
add_executable(strata-vision-gguf-test EXCLUDE_FROM_ALL gguf_meta_test.cpp)
target_link_libraries(strata-vision-gguf-test PRIVATE llama)
target_compile_features(strata-vision-gguf-test PRIVATE cxx_std_17)
set_target_properties(strata-vision-gguf-test PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
