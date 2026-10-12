/* embed FILE SYMBOL > out.c : write FILE as a C byte array named SYMBOL (plus SYMBOL_len). */
#include <stdio.h>
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    printf("const unsigned char %s[] = {", argv[2]);
    unsigned long n = 0;
    int c;
    while ((c = fgetc(f)) != EOF) { if (n % 16 == 0) printf("\n"); printf("%d,", c); n++; }
    printf("0};\nconst unsigned long %s_len = %lu;\n", argv[2], n);
    fclose(f);
    return 0;
}
