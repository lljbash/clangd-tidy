#include <cstddef>
bool foo(int* p){
  if(p==NULL){
  }
  return 1;
}
int main(){
  foo(NULL);
  return 0;
}
