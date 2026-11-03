// Passive UI-clock accounting. It neither grants leases nor changes video.
// Metadata is retained with each of the three buffers, then copied into the
// single read lease. Only the scheduler's qualified COMPLETE scan can publish
// an event. The four-entry journal makes snapshot loss detectable by the host.
module sr_identity_tracker(
    input wire clk,resetn,invalidate,input wire [63:0] ticks,
    input wire claim_write,input wire [1:0] write_index,
    input wire marker_valid,marker_invalid,input wire [80:0] marker_identity,
    input wire publish,publish_good,
    input wire claim_read,input wire [1:0] read_index,input wire [63:0] read_time,
    input wire scan_complete,read_release,
    output wire [1663:0] status
);
    reg [80:0] identity[0:2];
    reg [2:0] marked=0,committed=0,seen=0;
    reg writer_live=0,reader_live=0,reader_marked=0,reader_scanned=0;
    reg [1:0] writer=0,reader=0;
    reg [80:0] reader_identity=0;
    reg [63:0] reader_start=0;
    reg [31:0] valid_inputs=0,control_inputs=0,marker_errors=0,written_results=0;
    reg [31:0] result_scans=0,new_results=0,repeat_results=0,identity_errors=0;
    reg [31:0] unmarked_scans=0,incomplete_scans=0,control_scans=0,sequence_skips=0,discarded_buffers=0;
    reg [31:0] last_generation=0,last_sequence=0,last_mode_scale=0,last_latency=0,max_latency=0;
    reg last_valid=0,saturated=0,contract_fault=0;
    reg [255:0] journal[0:3];
    wire result_present=reader_identity[80];
    wire [7:0] mode=reader_identity[79:72],scale=reader_identity[71:64];
    wire [31:0] generation=reader_identity[63:32],source_sequence=reader_identity[31:0];
    wire [31:0] mode_scale={16'b0,scale,mode};
    wire same_generation=last_valid && generation==last_generation;
    wire stale=last_valid && (generation<last_generation ||
        (same_generation && (source_sequence<last_sequence || mode_scale!=last_mode_scale)));
    wire repeated=same_generation && source_sequence==last_sequence;
    wire [63:0] latency=ticks-reader_start;
    wire [31:0] skipped=same_generation && source_sequence>last_sequence ? source_sequence-last_sequence-1'b1 : 0;
    function automatic [31:0] add(input [31:0] value,input [31:0] increment);
        reg [32:0] sum;begin sum={1'b0,value}+{1'b0,increment};add=sum[32]?32'hffffffff:sum[31:0];end
    endfunction
    integer i;
    reg [2:0] discards;
    always @* begin
        discards=0;
        for(integer k=0;k<3;k=k+1)
            if(committed[k] && marked[k] && identity[k][80] && !seen[k])discards=discards+1'b1;
    end
    assign status={journal[3],journal[2],journal[1],journal[0],
        discarded_buffers,sequence_skips,max_latency,last_latency,last_mode_scale,last_sequence,last_generation,
        control_scans,incomplete_scans,unmarked_scans,identity_errors,repeat_results,new_results,
        result_scans,written_results,marker_errors,control_inputs,valid_inputs,
        28'b0,last_valid,contract_fault,saturated,1'b1,32'h53524944};
    always @(posedge clk)begin
        if(!resetn)begin
            marked<=0;committed<=0;seen<=0;writer_live<=0;reader_live<=0;reader_marked<=0;reader_scanned<=0;
            writer<=0;reader<=0;reader_identity<=0;reader_start<=0;
            valid_inputs<=0;control_inputs<=0;marker_errors<=0;written_results<=0;
            result_scans<=0;new_results<=0;repeat_results<=0;identity_errors<=0;
            unmarked_scans<=0;incomplete_scans<=0;control_scans<=0;sequence_skips<=0;discarded_buffers<=0;
            last_generation<=0;last_sequence<=0;last_mode_scale<=0;last_latency<=0;max_latency<=0;
            last_valid<=0;saturated<=0;contract_fault<=0;
            for(i=0;i<3;i=i+1)identity[i]<=0;
            for(i=0;i<4;i=i+1)journal[i]<=0;
        end else begin
            if(&valid_inputs || &control_inputs || &marker_errors || &written_results || &result_scans ||
                &new_results || &repeat_results || &identity_errors || &unmarked_scans || &incomplete_scans ||
                &control_scans || &sequence_skips || &discarded_buffers)saturated<=1;
            if(invalidate)begin
                if(writer_live || reader_live)contract_fault<=1;
                discarded_buffers<=add(discarded_buffers,{29'b0,discards});
                marked<=0;committed<=0;seen<=0;
            end
            if(claim_write)begin
                if(writer_live || write_index==3 || (reader_live && write_index==reader) || invalidate)contract_fault<=1;
                else begin
                    writer_live<=1;writer<=write_index;marked[write_index]<=0;committed[write_index]<=0;seen[write_index]<=0;
                    if(committed[write_index] && marked[write_index] && identity[write_index][80] && !seen[write_index])
                        discarded_buffers<=add(discarded_buffers,1);
                end
            end
            if(marker_valid || marker_invalid)begin
                if(!writer_live || (marker_valid && marker_invalid))contract_fault<=1;
                else begin
                    marked[writer]<=marker_valid;identity[writer]<=marker_identity;
                    if(marker_invalid)marker_errors<=add(marker_errors,1);
                    else if(marker_identity[80])valid_inputs<=add(valid_inputs,1);
                    else control_inputs<=add(control_inputs,1);
                end
            end
            if(publish)begin
                if(!writer_live)contract_fault<=1;
                else begin
                    writer_live<=0;committed[writer]<=publish_good;
                    if(publish_good && (marker_valid || (marked[writer] && !marker_invalid)) &&
                            (marker_valid ? marker_identity[80] : identity[writer][80]))written_results<=add(written_results,1);
                end
            end
            if(claim_read)begin
                if(reader_live || read_index==3 || !committed[read_index] || invalidate)contract_fault<=1;
                else begin
                    reader_live<=1;reader<=read_index;reader_identity<=identity[read_index];
                    reader_marked<=marked[read_index];reader_scanned<=0;reader_start<=read_time;
                end
            end
            if(scan_complete)begin
                if(!reader_live || reader_scanned)contract_fault<=1;
                else begin
                    reader_scanned<=1;
                    if(!reader_marked)unmarked_scans<=add(unmarked_scans,1);
                    else if(!result_present)control_scans<=add(control_scans,1);
                    else begin
                        result_scans<=add(result_scans,1);seen[reader]<=1;
                        if(stale)identity_errors<=add(identity_errors,1);
                        else if(repeated)repeat_results<=add(repeat_results,1);
                        else begin
                            new_results<=add(new_results,1);
                            sequence_skips<=add(sequence_skips,skipped);
                            last_valid<=1;last_generation<=generation;last_sequence<=source_sequence;last_mode_scale<=mode_scale;
                            if(|latency[63:32])begin saturated<=1;last_latency<=32'hffffffff;max_latency<=32'hffffffff;end
                            else begin last_latency<=latency[31:0];if(latency[31:0]>max_latency)max_latency<=latency[31:0];end
                            journal[new_results[1:0]]<={ticks,latency,mode_scale,source_sequence,generation,add(new_results,1)};
                        end
                    end
                end
            end
            if(read_release)begin
                if(!reader_live)contract_fault<=1;
                else begin
                    reader_live<=0;
                    if(!reader_scanned && !scan_complete)incomplete_scans<=add(incomplete_scans,1);
                end
            end
        end
    end
endmodule
