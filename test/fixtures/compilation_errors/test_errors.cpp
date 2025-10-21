#include <cstddef>

// This file contains three types of issues:
// 1. Syntax error (will cause compilation to fail)
// 2. Compiler warning (unused variable)
// 3. clang-tidy diagnostic (modernize-use-nullptr)

void function_with_issues() {
    // Issue #2: Compiler warning - unused variable
    int unused_var = 42;
    
    // Issue #3: clang-tidy diagnostic - use nullptr instead of NULL
    int* ptr = NULL;
    
    if (ptr == NULL) {
        // Do something
    }
}

int main() {
    // Issue #1: Syntax error - missing semicolon
    int x = 5
    
    return 0;
}
