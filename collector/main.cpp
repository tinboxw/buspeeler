// Receive-only collector. No transmit entry point is loaded or implemented.
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#define CALL __stdcall
#else
#include <dlfcn.h>
#include <unistd.h>
#define CALL
#endif
#ifdef __linux__
#include <linux/can.h>
#include <linux/can/raw.h>
#include <linux/can/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <poll.h>
#endif

static std::atomic<bool> running{true};
static void stop(int) { running = false; }
static double now() {
    return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
}
static void emit(uint32_t id, bool extended, bool remote, bool error,
                 const uint8_t* data, unsigned length, const std::string& channel, int64_t hardware_ticks=-1) {
    std::cout << std::setprecision(17) << "{\"timestamp\":" << now()
              << ",\"channel\":\"" << channel << "\",\"can_id\":" << id
              << ",\"extended\":" << (extended?"true":"false")
              << ",\"remote\":" << (remote?"true":"false")
              << ",\"error\":" << (error?"true":"false")
              << ",\"fd\":false,\"time_source\":\"collector_wall\",\"dropped\":null,\"hardware_timestamp\":";
    std::cout << "null,\"hardware_ticks\":";
    if (hardware_ticks < 0) std::cout << "null"; else std::cout << hardware_ticks;
    std::cout << ",\"dlc\":" << length << ",\"data\":\"";
    if (!remote) for (unsigned i=0; i<length; ++i)
        std::cout << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(data[i]);
    std::cout << std::dec << "\"}" << std::endl;
}

struct Library {
#ifdef _WIN32
    HMODULE handle;
    explicit Library(const std::string& path) {
        auto n = MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, nullptr, 0);
        std::vector<wchar_t> wide(n);
        MultiByteToWideChar(CP_UTF8, 0, path.c_str(), -1, wide.data(), n);
        handle = LoadLibraryExW(wide.data(), nullptr, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if (!handle) throw std::runtime_error("SDK load failed (architecture/dependencies/path)");
    }
    ~Library() { FreeLibrary(handle); }
    void* symbol(const char* name) { return reinterpret_cast<void*>(GetProcAddress(handle,name)); }
#else
    void* handle;
    explicit Library(const std::string& path) : handle(dlopen(path.c_str(),RTLD_NOW|RTLD_LOCAL)) {
        if (!handle) throw std::runtime_error(dlerror());
    }
    ~Library() { dlclose(handle); }
    void* symbol(const char* name) { return dlsym(handle,name); }
#endif
    template<class T> T get(const char* name) {
        auto p = symbol(name);
        if (!p) throw std::runtime_error(std::string("SDK symbol missing: ")+name);
        return reinterpret_cast<T>(p);
    }
};

// Legacy ControlCAN ABI only. Modern ZCAN and unrelated SDKs are not compatible.
struct Init { uint32_t acc_code, acc_mask, reserved; uint8_t filter, timing0, timing1, mode; };
struct Object { uint32_t id, timestamp; uint8_t time_flag, send_type, remote, extended, length, data[8], reserved[3]; };
static_assert(sizeof(Init)==16 && sizeof(Object)==24, "ControlCAN ABI mismatch");

static void vendor(const std::map<std::string,std::string>& args) {
    if (args.at("verified") != "1") throw std::runtime_error("Listen-only qualification required");
    Library lib(args.at("library"));
    using Open = uint32_t(CALL*)(uint32_t,uint32_t,uint32_t);
    using Close = uint32_t(CALL*)(uint32_t,uint32_t);
    using Initialize = uint32_t(CALL*)(uint32_t,uint32_t,uint32_t,Init*);
    using Receive = uint32_t(CALL*)(uint32_t,uint32_t,uint32_t,Object*,uint32_t,int32_t);
    auto open=lib.get<Open>("VCI_OpenDevice"); auto close=lib.get<Close>("VCI_CloseDevice");
    auto initialize=lib.get<Initialize>("VCI_InitCAN"); auto start=lib.get<Open>("VCI_StartCAN");
    auto reset=lib.get<Open>("VCI_ResetCAN"); auto receive=lib.get<Receive>("VCI_Receive");
    auto index=static_cast<uint32_t>(std::stoul(args.at("index")));
    auto requested_channel=std::stoi(args.at("channel"));
    std::vector<uint32_t> channels=requested_channel==-1?std::vector<uint32_t>{0,1}:std::vector<uint32_t>{static_cast<uint32_t>(requested_channel)};
    auto type=static_cast<uint32_t>(std::stoul(args.at("type")));
    if (requested_channel < -1 || requested_channel>1) throw std::runtime_error("Channel must be -1, 0 or 1");
    if (open(type,index,0)!=1) throw std::runtime_error("Device open failed");
    try {
        Init config{0,0xffffffffu,0,1,static_cast<uint8_t>(std::stoul(args.at("timing0"))),
                    static_cast<uint8_t>(std::stoul(args.at("timing1"))),1}; // mode=1 listen only
        for(auto channel:channels) {
            if (initialize(type,index,channel,&config)!=1 || start(type,index,channel)!=1)
                throw std::runtime_error("Listen-only initialization failed");
        }
        std::cerr << "READY listen-only legacy-controlcan\n" << std::flush;
        Object objects[256];
        while (running) {
          for(auto channel:channels) {
            auto count=receive(type,index,channel,objects,256,10);
            if (count==0xffffffffu || count>256) throw std::runtime_error("Receive/device failure");
            for (uint32_t i=0;i<count;++i) {
                auto& f=objects[i];
                if (f.length>8) throw std::runtime_error("SDK returned invalid length");
                // Vendor timestamp units/wrap are not qualified: retain unknown rather than guess.
                emit(f.id,f.extended!=0,f.remote!=0,false,f.data,f.length,"can"+std::to_string(channel),f.time_flag?f.timestamp:-1LL);
            }
            if (!count) std::this_thread::sleep_for(std::chrono::milliseconds(2));
          }
        }
        for(auto channel:channels) reset(type,index,channel);
    } catch (...) { close(type,index); throw; }
    close(type,index);
}

#ifdef __linux__
static bool silent(unsigned index) {
    int fd=socket(AF_NETLINK,SOCK_RAW,NETLINK_ROUTE);
    if(fd<0) return false;
    struct { nlmsghdr header; ifinfomsg info; } request{};
    request.header.nlmsg_len=NLMSG_LENGTH(sizeof(ifinfomsg));
    request.header.nlmsg_type=RTM_GETLINK; request.header.nlmsg_flags=NLM_F_REQUEST;
    request.info.ifi_family=AF_UNSPEC; request.info.ifi_index=static_cast<int>(index);
    sockaddr_nl address{}; address.nl_family=AF_NETLINK;
    if(sendto(fd,&request,request.header.nlmsg_len,0,reinterpret_cast<sockaddr*>(&address),sizeof(address))<0) { close(fd); return false; }
    pollfd wait{fd,POLLIN,0};
    if(poll(&wait,1,1000)<=0) { close(fd); return false; }
    char buffer[8192]; int size=static_cast<int>(recv(fd,buffer,sizeof(buffer),0)); close(fd);
    for(auto* h=reinterpret_cast<nlmsghdr*>(buffer); NLMSG_OK(h,size); h=NLMSG_NEXT(h,size)) {
        if(h->nlmsg_type!=RTM_NEWLINK) continue;
        auto* info=static_cast<ifinfomsg*>(NLMSG_DATA(h));
        if(!(info->ifi_flags & IFF_UP)) return false;
        int length=IFLA_PAYLOAD(h);
        for(auto* attr=IFLA_RTA(info); RTA_OK(attr,length); attr=RTA_NEXT(attr,length)) {
            if(attr->rta_type!=IFLA_LINKINFO) continue;
            int inner=RTA_PAYLOAD(attr);
            for(auto* a=static_cast<rtattr*>(RTA_DATA(attr)); RTA_OK(a,inner); a=RTA_NEXT(a,inner)) {
                if(a->rta_type!=IFLA_INFO_DATA) continue;
                int data_length=RTA_PAYLOAD(a);
                for(auto* b=static_cast<rtattr*>(RTA_DATA(a)); RTA_OK(b,data_length); b=RTA_NEXT(b,data_length)) {
                    if(b->rta_type==IFLA_CAN_CTRLMODE && RTA_PAYLOAD(b)>=sizeof(can_ctrlmode)) {
                        auto* mode=static_cast<can_ctrlmode*>(RTA_DATA(b));
                        return (mode->flags & CAN_CTRLMODE_LISTENONLY)!=0 && (mode->flags & CAN_CTRLMODE_FD)==0;
                    }
                }
            }
        }
    }
    return false;
}
static void socketcan(const std::string& name) {
    if(name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")!=std::string::npos)
        throw std::runtime_error("Invalid interface name");
    auto index=if_nametoindex(name.c_str());
    if(!index || !silent(index)) throw std::runtime_error("Interface must be UP, classical CAN and kernel-confirmed LISTEN-ONLY");
    int fd=socket(PF_CAN,SOCK_RAW,CAN_RAW);
    if(fd<0) throw std::runtime_error("CAN socket failed");
    sockaddr_can address{}; address.can_family=AF_CAN; address.can_ifindex=static_cast<int>(index);
    if(bind(fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))<0) { close(fd); throw std::runtime_error("CAN bind failed"); }
    can_err_mask_t errors=CAN_ERR_MASK;
    setsockopt(fd,SOL_CAN_RAW,CAN_RAW_ERR_FILTER,&errors,sizeof(errors));
    std::cerr << "READY listen-only socketcan\n" << std::flush;
    auto checked=std::chrono::steady_clock::now();
    while(running) {
        if(std::chrono::steady_clock::now()-checked>std::chrono::seconds(1)) {
            if(!silent(index)) { close(fd); throw std::runtime_error("Listen-only configuration changed or device lost"); }
            checked=std::chrono::steady_clock::now();
        }
        pollfd p{fd,POLLIN,0}; int ready=poll(&p,1,100);
        if(ready<0 && running) { close(fd); throw std::runtime_error("CAN poll failed"); }
        if(ready<=0) continue;
        if(p.revents & (POLLERR|POLLHUP|POLLNVAL)) { close(fd); throw std::runtime_error("CAN device lost"); }
        can_frame frame{}; auto n=read(fd,&frame,sizeof(frame));
        if(n!=sizeof(frame)) { close(fd); throw std::runtime_error("CAN frame read failed"); }
        emit(frame.can_id & CAN_EFF_MASK,frame.can_id & CAN_EFF_FLAG,frame.can_id & CAN_RTR_FLAG,
             frame.can_id & CAN_ERR_FLAG,frame.data,frame.can_dlc,name);
    }
    close(fd);
}
#endif

int main(int argc,char** argv) {
    std::signal(SIGINT,stop); std::signal(SIGTERM,stop);
    std::thread([] { std::string line; while(std::getline(std::cin,line)) { if(line=="stop") break; } running=false; }).detach();
#ifdef __linux__
    auto parent=getppid(); prctl(PR_SET_PDEATHSIG,SIGTERM); if(getppid()!=parent) return 2;
#endif
    try {
        std::map<std::string,std::string> args;
        for(int i=1;i+1<argc;i+=2) args[argv[i]]=argv[i+1];
        if(args.count("backend") && args.at("backend")=="socketcan") {
#ifdef __linux__
            socketcan(args.at("interface"));
#else
            throw std::runtime_error("SocketCAN is Linux only");
#endif
        } else if(args.count("backend") && (args.at("backend")=="zlg" || args.at("backend")=="chuangxin")) vendor(args);
        else throw std::runtime_error("Expected backend socketcan|zlg|chuangxin; live CAN FD is not enabled");
        return 0;
    } catch(const std::exception& e) { std::cerr << "ERROR " << e.what() << std::endl; return 2; }
}
