// strata/platform/thread_name.hpp - names the engine's long-lived threads for `ps -L -o tid,psr,comm` and
// /proc/<pid>/task/<tid>/comm. A name changes nothing the thread does.
#pragma once

#include <pthread.h>

#include <cstdio>

namespace strata::platform {

/// Gives the calling thread a name: "strata-pool-3" for `name_this_thread("strata-pool", 3)`, "strata-host" for no index.
/// Linux keeps 15 characters, the rest is cut.  Never fails loudly: a system that cannot name a thread leaves it unnamed.
inline void name_this_thread(const char* base, int index = -1) {
    char name[64];
    if (index >= 0) std::snprintf(name, sizeof name, "%s-%d", base, index);
    else std::snprintf(name, sizeof name, "%s", base);
    name[15] = '\0';
    (void) pthread_setname_np(pthread_self(), name);
}

}  // namespace strata::platform
