// Test file with non-ASCII UTF-8 characters: café, naïve, 日本語
#include <cstddef>

// Function with UTF-8 comment: Москва, Αθήνα, 北京
void test_function(int* ptr){
  const char* greeting="Héllo Wörld";  // UTF-8 string: José, François
  // Check pointer - should suggest nullptr
  if(ptr==NULL){
    return;
  }
  
  // UTF-8 characters BEFORE the fix target on the same line
  // This tests that byte offset calculation handles multi-byte UTF-8 correctly
  int* x=/* café */ NULL;  // café is 5 chars but 6 bytes (é = 2 bytes)
  int* y=/* 日本語 */ NULL;  // 3 chars but 9 bytes (each Japanese char = 3 bytes)
}

int main(){
  test_function(NULL);  // Should be fixed to nullptr
  return 0;
}
