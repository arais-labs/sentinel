# Targeted packaging copies only the distro library owner's payload. Apply the
# same empty install RPATH as upstream's generated CMake install scripts.
file(GLOB libraries "${LIBRARY_DIR}/lib*.so*")
foreach(library IN LISTS libraries)
    if(NOT IS_SYMLINK "${library}")
        file(RPATH_REMOVE FILE "${library}")
    endif()
endforeach()
