`timescale 1ns/1ps
module tb_bridge_pipeline;
    reg ui_clk=0,pixel_clk=0,resetn=0,pixel_resetn=0;
    always #5 ui_clk=~ui_clk;
    always #3.697 pixel_clk=~pixel_clk; // intentional phase/rate drift
    reg prepare=0,boundary=0,external_active=0,frame_complete=0,frame_failed=0;
    reg scan_link_valid=1;
    wire fifo_prefilled,fifo_empty,fifo_reset_busy,fifo_received,fifo_retired,fifo_bad;
    wire fifo_start,fifo_abort,fifo_release,external_enable,mm2s_fsync;
    wire [31:0] expected_frame,expected_crc;
    reg source_offer=0,source_discontinuity=0,producer_good=1,pause_source=0;
    wire source_accept;wire [31:0] producer_crc;
    integer source_timer=0;
    always @(posedge ui_clk)begin
        source_offer<=0;
        if(!resetn)source_timer<=0;
        else if(source_timer==1777)begin source_timer<=0;source_offer<=!pause_source;end
        else source_timer<=source_timer+1;
    end
    always @(posedge pixel_clk)if(fifo_start&&expected_crc!==(32'hc3210042^expected_frame))$fatal(1,"Mixed frame CRC descriptor");
    reg wr_ready=1,rd_ready=1,wr_armed=0,rd_armed=0,wr_done=0,rd_done=0,control_fault=0;
    wire wr_start,rd_start,wr_complete,rd_complete,producer_start;
    wire [31:0] wr_base,rd_base;wire [15:0] producer_frame;
    assign producer_crc=32'hc3210042^{16'b0,producer_frame};
    wire producer_done,producer_fault,fault;
    wire [17:0] owner_states;
    wire [31:0] source_frames,written_frames,read_frames,displayed_frames,new_frames,repeated_frames,dropped_frames;
    wire [31:0] max_latency_cycles,last_displayed_frame,epoch;wire [63:0] ticks;
    bridge_pipeline_control #(.PRODUCE_CYCLES(1778),.READ_WINDOW_CYCLES(110),.FRAME_BYTES(2048)) dut(
        .marker_valid(1'b0),.marker_invalid(1'b0),.marker_identity(81'b0),.sr_status(),.*);
    wire [31:0] source_data;wire [3:0] source_keep;wire source_valid,source_user,source_last,producer_busy;
    video_pattern_axis #(.WIDTH(64),.HEIGHT(8)) source(
        .clk(ui_clk),.resetn(resetn),.start(producer_start),.frame(producer_frame),.busy(producer_busy),
        .done(producer_done),.fault(producer_fault),.tdata(source_data),.tkeep(source_keep),
        .tvalid(source_valid),.tuser(source_user),.tlast(source_last),.tready(1'b1));
    reg [31:0] memory[0:1535];integer write_index=0,write_bank=0,write_delay=0,read_delay=0;
    integer write_stop=0,read_stop=0;
    reg [31:0] random_state=32'h9a58c417;
    wire writer_conflict=dut.writer_active&&dut.reader_active&&dut.writer_index==dut.reader_index;
    always @(posedge ui_clk)begin
        random_state<=random_state^(random_state<<13)^((random_state^(random_state<<13))>>17);
        wr_done<=0;rd_done<=0;
        if(!resetn)begin
            wr_ready<=1;rd_ready<=1;wr_armed<=0;rd_armed<=0;write_delay<=0;read_delay<=0;write_stop<=0;read_stop<=0;
        end else begin
            if(wr_start)begin wr_ready<=0;write_delay<=8;write_index<=0;write_bank<=wr_base/4;end
            if(rd_start)begin rd_ready<=0;read_delay<=8;end
            if(write_delay>0)begin write_delay<=write_delay-1;if(write_delay==1)wr_armed<=1;end
            if(read_delay>0)begin read_delay<=read_delay-1;if(read_delay==1)rd_armed<=1;end
            if(source_valid)begin
                if(!wr_armed||write_index>=512)$fatal(1,"Producer outside write lease");
                memory[write_bank+write_index]<=source_data;write_index<=write_index+1;
            end
            if(producer_done)write_stop<=37;
            if(write_stop>0)begin
                write_stop<=write_stop-1;
                if(write_stop==1)begin wr_done<=1;wr_ready<=1;wr_armed<=0;end
            end
            if(rd_armed&&rd_complete&&read_stop==0)begin read_stop<=29;rd_armed<=0;end
            if(read_stop>0)begin read_stop<=read_stop-1;if(read_stop==1)begin rd_done<=1;rd_ready<=1;end end
            if(writer_conflict)$fatal(1,"Writer reused a retained scan/prefetch buffer");
        end
    end
    reg prior_fsync=0,reading=0,starve=0;integer read_index=0,read_bank=0;
    wire input_ready;wire [31:0] input_data=memory[read_bank+read_index];
    wire input_valid=reading&&!starve;
    wire [47:0] pair_data;wire pair_valid;reg pair_pop=0;
    wire fifo_busy;wire [31:0] pixels_received,pairs_consumed;
    video_pair_fifo #(.WIDTH(64),.HEIGHT(8),.DEPTH(64),.PREFILL(16)) fifo(
        .clk(pixel_clk),.resetn(pixel_resetn),.start(fifo_start),.abort(fifo_abort),.release_frame(fifo_release),
        .tdata(input_data),.tkeep(4'hf),.tvalid(input_valid),.tuser(read_index==0),.tlast(read_index%64==63),
        .tready(input_ready),.pair_data(pair_data),.pair_valid(pair_valid),.pair_pop(pair_pop),
        .prefilled(fifo_prefilled),.empty(fifo_empty),.reset_busy(fifo_reset_busy),
        .busy(fifo_busy),.received(fifo_received),.retired(fifo_retired),.bad(fifo_bad),.*);
    reg [31:0] scan_memory[0:511];integer scan_pairs=0,position=0,scans=0;reg compare=0;
    always @(posedge ui_clk)if(resetn&&fault&&!starve)
        $fatal(1,"Unexpected fault owner=%b fifo=%b frame=%b writer=%b reader=%b written=%d read=%d scans=%d pos=%d pending=%b refs=%b",
            dut.owner_fault,fifo_bad,frame_failed,dut.writer_active,dut.reader_active,written_frames,read_frames,scans,position,dut.pending_px,dut.refs);
    function automatic [23:0] rgb(input [31:0] v);rgb={v[7:0],v[15:8],v[23:16]};endfunction
    always @(negedge pixel_clk)pair_pop=pixel_resetn&&external_active&&position>=10&&position<778&&((position-10)%3==0)&&pair_valid;
    always @(posedge pixel_clk)begin
        boundary<=0;prepare<=0;frame_complete<=0;prior_fsync<=mm2s_fsync;
        if(!pixel_resetn)begin position<=0;external_active<=0;frame_failed<=0;reading<=0;scans<=0;scan_pairs<=0;compare<=0;end
        else begin
            if(prior_fsync&&!mm2s_fsync)begin
                if(reading)$fatal(1,"Read descriptor overlap");
                read_index<=0;read_bank<=rd_base/4;reading<=1;
                for(integer i=0;i<512;i=i+1)scan_memory[i]=memory[rd_base/4+i];
            end
            if(input_valid&&input_ready)begin
                if(read_index==511)reading<=0;else read_index<=read_index+1;
            end
            position<=position==1199?0:position+1;
            if(position==0)begin boundary<=1;external_active<=external_enable;frame_failed<=0;scan_pairs<=0;compare<=external_enable;end
            if(external_active&&position>=10&&position<778&&(position-10)%3==0)begin
                if(!pair_valid)frame_failed<=1;
                else begin
                    if(!frame_failed&&pair_data!=={rgb(scan_memory[scan_pairs*2]),rgb(scan_memory[scan_pairs*2+1])})
                        $fatal(1,"Mixed/corrupt pixels in complete scan %d pair %d",scans,scan_pairs);
                    scan_pairs<=scan_pairs+1;
                end
            end
            if(position==1000)begin
                prepare<=1;
                if(external_active&&!frame_failed)begin
                    if(scan_pairs!=256)$fatal(1,"Wrong pair count");frame_complete<=1;scans<=scans+1;
                end
            end
        end
    end
    initial begin
        repeat(20)@(negedge ui_clk);resetn=1;
        repeat(20)@(negedge pixel_clk);pixel_resetn=1;
        wait(scans>=100);
        repeat(30)@(negedge ui_clk);
        if(fault||new_frames<40||repeated_frames<40||written_frames<45||dropped_frames!=0)
            $fatal(1,"Concurrent scheduling failed new=%d repeat=%d write=%d drop=%d",new_frames,repeated_frames,written_frames,dropped_frames);
        $display("PROGRESS: 100 complete scans with clock drift, delayed responses and retained references");
        begin
            integer previous;previous=displayed_frames;scan_link_valid=0;
            wait(scans>=105);repeat(30)@(negedge ui_clk);
            if(displayed_frames!=previous||fault)$fatal(1,"Link-invalid scans counted or corrupted leases");
            scan_link_valid=1;wait(scans>=107);repeat(30)@(negedge ui_clk);
            if(displayed_frames!=previous+2||fault)$fatal(1,"Valid scanning did not resume");
        end
        // Invalidate while AXI and scan references remain outstanding.
        pause_source=1;wait(external_active&&position==200);
        @(negedge ui_clk);source_discontinuity=1;
        @(negedge ui_clk);source_discontinuity=0;
        wait(epoch==1);repeat(2500)@(negedge pixel_clk);
        if(fault||external_active||dut.writer_active||dut.reader_active)$fatal(1,"Loss did not retire safely");
        begin integer before_scan;before_scan=displayed_frames;
            repeat(3000)@(negedge ui_clk);
            if(displayed_frames!=before_scan)$fatal(1,"Old epoch resurrected");
            pause_source=0;wait(displayed_frames>=before_scan+8);
        end
        producer_good=0;wait(producer_done);producer_good=0;
        wait(epoch==2);producer_good=1;wait(external_active&&position==200);
        if(fault||dropped_frames==0)$fatal(1,"Bad input frame not retired");
        starve=1;
        wait(fault);repeat(2500)@(negedge pixel_clk);
        if(external_active)$fatal(1,"Failed output did not switch to diagnostic");
        begin
            integer previous;previous=new_frames;
            repeat(3000)@(negedge ui_clk);
            if(new_frames!=previous)$fatal(1,"Failed frame counted as new display");
        end
        $display("PASS: bridge_pipeline external offers, CRC descriptors, source loss/invalid frame retirement, concurrent leases, repeat accounting, CDC, link qualification, underflow fail-closed");$finish;
    end
    initial begin #50000;$display("DEBUG source=%d drop=%d ticks=%d reset=%b fault=%b owner_wr=%b ref=%b wfound=%b ready=%b count=%d",source_frames,dropped_frames,ticks,resetn,fault,dut.owner_wr_ready,dut.refs,dut.owner.wfound,wr_ready,dut.produce_count);end
    initial begin #4000000;$fatal(1,"Pipeline timeout written=%d read=%d scans=%d owner=%h window=%d reader=%b sent=%b armed=%b fifo=%b pixels=%d pending=%b sync=%b recv=%b",
        written_frames,read_frames,scans,owner_states,dut.read_window,dut.reader_active,dut.reader_sent,rd_armed,fifo_busy,
        pixels_received,dut.pending_px,mm2s_fsync,rd_complete);end
endmodule
