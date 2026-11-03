// UI-domain three-frame leases. ref_busy includes AXI, queued commands, FIFO
// prefetched pixels AND a consumer still scanning the buffer. No lease is free
// merely because an AXI RLAST or video TLAST arrived. Global reset is legal only
// with the attached masters/FIFOs reset; source loss uses invalidate, not reset.
// N4 conservative allocation: only FREE buffers may be written. READY buffers
// are never reclaimed by input admission, removing read-selection -> write-grant
// combinational coupling. Buffer exhaustion drops the new whole input frame.
module bridge_frame_owner(
    input wire clk,resetn,invalidate,input wire [2:0] ref_busy,
    input wire wr_request,input wire [31:0] source_frame,input wire [63:0] source_time,
    output wire wr_ready,output reg [1:0] wr_index,output wire [31:0] current_epoch,
    input wire wr_done,wr_ok,
    input wire rd_request,output wire rd_ready,output reg [1:0] rd_index,
    output wire [31:0] rd_frame,rd_epoch,output wire [63:0] rd_time,
    input wire rd_commit,rd_cancel, // commit only at the downstream safe frame boundary
    output reg pending=0,scan_valid=0,output reg [1:0] scan_index=0,
    output reg [31:0] scan_frame=0,scan_epoch=0,new_frames=0,repeated_frames=0,reclaimed_frames=0,
    output reg fault=0,output wire [17:0] states
);
    localparam FREE=0,WRITING=1,READY=2,PENDING=3,SCANNING=4,RETIRING=5;
    reg [2:0] state[0:2];reg [31:0] generation[0:2],frame_id[0:2];
    // A permutation of 0,1,2 records recency (0 is newest). Updating it on
    // writer acquisition preserves completion order because only one writer
    // exists, and avoids chained 32-bit subtractors in the lease grant path.
    reg [1:0] recency[0:2];
    reg [63:0] timestamp[0:2];reg [31:0] epoch=0;
    reg writing=0;reg [1:0] writer=0,reserved=0;reg repeated=0;
    reg wfound,rfound;integer i;
    assign current_epoch=epoch;
    assign states={3'b0,state[2],3'b0,state[1],3'b0,state[0]};
    assign wr_ready=wfound && !writing && !invalidate && !fault;
    assign rd_ready=rfound && !pending && !invalidate && !fault;
    assign rd_frame=frame_id[rd_index];assign rd_epoch=generation[rd_index];assign rd_time=timestamp[rd_index];
    always @* begin
        rfound=0;rd_index=0;
        for(integer j=0;j<3;j=j+1)if(state[j]==READY && generation[j]==epoch)begin
            if(!rfound || recency[j]<recency[rd_index])begin rfound=1;rd_index=j;end
        end
        if(!rfound && scan_valid && generation[scan_index]==epoch)begin rfound=1;rd_index=scan_index;end
        wfound=0;wr_index=0;
        for(integer j=0;j<3;j=j+1)if(state[j]==FREE && !ref_busy[j] && !wfound)begin wfound=1;wr_index=j;end

    end
    always @(posedge clk) begin
        if(!resetn)begin
            for(i=0;i<3;i=i+1)begin state[i]<=FREE;generation[i]<=0;frame_id[i]<=0;recency[i]<=i;timestamp[i]<=0;end
            epoch<=0;writing<=0;pending<=0;scan_valid<=0;fault<=0;
            new_frames<=0;repeated_frames<=0;reclaimed_frames<=0;scan_index<=0;scan_frame<=0;scan_epoch<=0;
        end else if(invalidate)begin
            epoch<=epoch+1'b1;pending<=0;scan_valid<=0;
            for(i=0;i<3;i=i+1)state[i]<=RETIRING;
            if(wr_done)writing<=0; // a simultaneous old completion is retired, never published
        end else if(!fault)begin
            for(i=0;i<3;i=i+1)if(state[i]==RETIRING && !ref_busy[i] && !(writing && writer==i))state[i]<=FREE;
            if(wr_done)begin
                if(!writing)fault<=1;
                else begin
                    writing<=0;
                    if(ref_busy[writer])begin fault<=1;state[writer]<=RETIRING;end
                    else if(wr_ok && generation[writer]==epoch && state[writer]==WRITING)state[writer]<=READY;
                    else state[writer]<=RETIRING;
                end
            end
            if(wr_request && wr_ready)begin
                if(state[wr_index]==READY)reclaimed_frames<=reclaimed_frames+1'b1;
                state[wr_index]<=WRITING;writing<=1;writer<=wr_index;generation[wr_index]<=epoch;
                frame_id[wr_index]<=source_frame;timestamp[wr_index]<=source_time;
                recency[wr_index]<=0;
                for(i=0;i<3;i=i+1)if(i!=wr_index&&recency[i]<recency[wr_index])recency[i]<=recency[i]+1'b1;
            end
            if(rd_request && rd_ready)begin
                pending<=1;reserved<=rd_index;repeated<=state[rd_index]==SCANNING;
                if(state[rd_index]==READY)state[rd_index]<=PENDING;
            end
            if(rd_cancel || rd_commit)begin
                if(!pending || (rd_cancel && rd_commit))fault<=1;
                else begin
                    pending<=0;
                    if(rd_cancel)begin if(!repeated)state[reserved]<=RETIRING;end
                    else if(generation[reserved]!=epoch)begin fault<=1;state[reserved]<=RETIRING;end
                    else begin
                        if(scan_valid && scan_index!=reserved)state[scan_index]<=RETIRING;
                        // A committed selection supersedes only older unclaimed
                        // READY frames. A newer completion during prefetch must
                        // survive for the next scan. Retirement waits for ref_busy
                        // like every other release. Use the registered lease,
                        // not combinational read selection in the write grant.
                        for(i=0;i<3;i=i+1)
                            if(state[i]==READY && i!=reserved && recency[i]>recency[reserved])state[i]<=RETIRING;
                        state[reserved]<=SCANNING;scan_valid<=1;scan_index<=reserved;
                        scan_frame<=frame_id[reserved];scan_epoch<=generation[reserved];
                        if(scan_valid && scan_frame==frame_id[reserved] && scan_epoch==generation[reserved])
                            repeated_frames<=repeated_frames+1'b1;
                        else new_frames<=new_frames+1'b1;
                    end
                end
            end
        end
    end
endmodule
