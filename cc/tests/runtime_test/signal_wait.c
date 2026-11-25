#include <stdio.h>
#include <unistd.h>

/* Report readiness only after exec, so the caller can stop a live native tool. */
int main(void) {
    printf("%ld\n", (long)getpid());
    fflush(stdout);
    for (;;) pause();
}
