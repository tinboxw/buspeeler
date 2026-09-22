#include <cstdint>
#ifdef _WIN32
#define API extern "C" __declspec(dllexport) uint32_t __stdcall
#else
#define API extern "C" uint32_t
#endif
struct Init { uint32_t a,b,c; uint8_t filter,t0,t1,mode; };
struct Object { uint32_t id,time; uint8_t flag,type,remote,extended,length,data[8],reserved[3]; };
static bool initialized[2]={false,false},sent[2]={false,false};
API VCI_OpenDevice(uint32_t,uint32_t,uint32_t){return 1;}
API VCI_CloseDevice(uint32_t,uint32_t){return 1;}
API VCI_InitCAN(uint32_t,uint32_t,uint32_t c,Init* config){if(c>1||config->mode!=1)return 0; initialized[c]=true;return 1;}
API VCI_StartCAN(uint32_t,uint32_t,uint32_t c){return c<2&&initialized[c]?1:0;}
API VCI_ResetCAN(uint32_t,uint32_t,uint32_t){return 1;}
API VCI_Receive(uint32_t,uint32_t,uint32_t c,Object* output,uint32_t,int32_t){
 if(c>1||!initialized[c])return 0xffffffff;
 if(sent[c])return 0;
 sent[c]=true;*output={};output->id=0x390;output->length=8;output->data[2]=1;return 1;
}
// Deliberately no VCI_Transmit symbol. Loading it would fail this test.
