/* A small JSON reader for the settings files (sgba_ui.json, controls.json). */
#ifndef JSON_H
#define JSON_H
#include <stddef.h>
typedef enum { J_NULL, J_BOOL, J_NUM, J_STR, J_ARR, J_OBJ } JType;
typedef struct JNode {
    JType type;
    double num;
    char *str;
    struct JNode **items;      /* array elements or object values */
    char **keys;               /* object keys */
    int count;
} JNode;
JNode *json_parse(const char *text);
void json_free(JNode *n);
JNode *json_get(const JNode *obj, const char *key);
const char *json_str(const JNode *n, const char *fallback);
double json_num(const JNode *n, double fallback);
int json_bool(const JNode *n, int fallback);
#endif
