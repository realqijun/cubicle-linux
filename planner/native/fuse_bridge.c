/* Thin libfuse3 callback bridge. All policy and filesystem operations are Python-owned. */
#define FUSE_USE_VERSION 31
#include <fuse.h>
#include <stdint.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum { ATTR=1, LIST, OPEN, READ, WRITE, CLOSE, MKDIR, UNLINK, RMDIR, RENAME, TRUNCATE, SYNC };
struct __attribute__((packed)) request {
    uint32_t op, flags, mode, count;
    uint64_t handle, offset;
    char path[4096], other[4096];
};
struct __attribute__((packed)) response { int32_t error; uint32_t count; };
static int channel;
static int transfer(void *data, size_t count, int output) {
    char *cursor=data;
    while(count) {
        ssize_t size=output?write(channel,cursor,count):read(channel,cursor,count);
        if(size<0 && errno==EINTR) continue;
        if(size<=0) return -EIO;
        cursor+=size;count-=size;
    }
    return 0;
}
static int rpc(struct request *request, const void *input, void **output) {
    struct response response;
    if(transfer(request,sizeof(*request),1)<0 ||
       (request->op==WRITE && transfer((void *)input,request->count,1)<0) ||
       transfer(&response,sizeof(response),0)<0) return -EIO;
    if(response.count>1024*1024) return -EIO;
    *output=calloc(response.count+1,1);
    if(!*output) return -ENOMEM;
    if(transfer(*output,response.count,0)<0) {free(*output);return -EIO;}
    if(response.error) {free(*output);return -response.error;}
    return response.count;
}
static struct request req(unsigned op,const char *path) {
    struct request result={.op=op};
    if(path) strncpy(result.path,path,sizeof(result.path)-1);
    return result;
}
static int simple(struct request *request) {
    void *data;int result=rpc(request,NULL,&data);
    if(result<0) return result;
    free(data);return 0;
}
static int attributes(const char *path,struct stat *info,struct fuse_file_info *file) {
    (void)file;struct request request=req(ATTR,path);void *data;
    int result=rpc(&request,NULL,&data);if(result<0)return result;
    if(result!=10*sizeof(uint64_t)){free(data);return -EIO;}
    uint64_t *values=data;memset(info,0,sizeof(*info));
    info->st_mode=values[0];info->st_ino=values[1];info->st_nlink=values[2];
    info->st_uid=values[3];info->st_gid=values[4];info->st_size=values[5];
    info->st_atim.tv_sec=values[6]/1000000000;info->st_atim.tv_nsec=values[6]%1000000000;
    info->st_mtim.tv_sec=values[7]/1000000000;info->st_mtim.tv_nsec=values[7]%1000000000;
    info->st_ctim.tv_sec=values[8]/1000000000;info->st_ctim.tv_nsec=values[8]%1000000000;
    info->st_blocks=values[9];free(data);return 0;
}
static int listing(const char *path,void *buffer,fuse_fill_dir_t fill,off_t offset,
                   struct fuse_file_info *file,enum fuse_readdir_flags flags) {
    (void)offset;(void)file;(void)flags;struct request request=req(LIST,path);void *data;
    int count=rpc(&request,NULL,&data);if(count<0)return count;
    fill(buffer,".",NULL,0,0);fill(buffer,"..",NULL,0,0);
    for(char *name=data;name<(char *)data+count;name+=strlen(name)+1)
        if(fill(buffer,name,NULL,0,0))break;
    free(data);return 0;
}
static int opening(const char *path,struct fuse_file_info *file) {
    struct request request=req(OPEN,path);request.flags=file->flags;request.mode=0600;void *data;
    int count=rpc(&request,NULL,&data);if(count<0)return count;
    if(count!=8){free(data);return -EIO;}
    memcpy(&file->fh,data,8);file->direct_io=1;file->keep_cache=0;free(data);return 0;
}
static int creating(const char *path,mode_t mode,struct fuse_file_info *file) {
    (void)mode;file->flags|=O_CREAT;return opening(path,file);
}
static int reading(const char *path,char *buffer,size_t count,off_t offset,struct fuse_file_info *file) {
    struct request request=req(READ,path);request.handle=file->fh;request.offset=offset;
    request.count=count>1024*1024?1024*1024:count;void *data;
    int result=rpc(&request,NULL,&data);if(result>=0){memcpy(buffer,data,result);free(data);}return result;
}
static int writing(const char *path,const char *buffer,size_t count,off_t offset,struct fuse_file_info *file) {
    struct request request=req(WRITE,path);request.handle=file->fh;request.offset=offset;
    request.count=count>1024*1024?1024*1024:count;void *data;
    int result=rpc(&request,buffer,&data);if(result<0)return result;
    if(result!=4){free(data);return -EIO;}uint32_t size;memcpy(&size,data,4);free(data);return size;
}
static int closing(const char *path,struct fuse_file_info *file) {
    struct request request=req(CLOSE,path);request.handle=file->fh;return simple(&request);
}
static int making(const char *path,mode_t mode){struct request r=req(MKDIR,path);r.mode=mode;return simple(&r);}
static int removing(const char *path){struct request r=req(UNLINK,path);return simple(&r);}
static int removing_dir(const char *path){struct request r=req(RMDIR,path);return simple(&r);}
static int moving(const char *path,const char *other,unsigned flags){struct request r=req(RENAME,path);r.flags=flags;strncpy(r.other,other,sizeof(r.other)-1);return simple(&r);}
static int truncating(const char *path,off_t size,struct fuse_file_info *file){struct request r=req(TRUNCATE,path);r.offset=size;r.handle=file?file->fh:0;return simple(&r);}
static int syncing(const char *path,int dataonly,struct fuse_file_info *file){(void)dataonly;struct request r=req(SYNC,path);r.handle=file->fh;return simple(&r);}
static void *initialize_fs(struct fuse_conn_info *connection, struct fuse_config *config) {
    (void)connection;
    config->direct_io = 1;
    config->hard_remove = 1;
    return NULL;
}
int main(int argc,char **argv) {
    if(argc<3) return 2;
    channel=atoi(argv[1]);
    argv[1]=argv[0];
    struct fuse_operations operations={.getattr=attributes,.readdir=listing,.open=opening,.create=creating,
        .read=reading,.write=writing,.release=closing,.mkdir=making,.unlink=removing,.rmdir=removing_dir,
        .rename=moving,.truncate=truncating,.fsync=syncing,.init=initialize_fs};
    return fuse_main(argc-1,argv+1,&operations,NULL);
}
