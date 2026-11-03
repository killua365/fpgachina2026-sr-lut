`timescale 1ns/1ps
module tb_sr_identity_core;
    reg ui_clk=0,pixel_clk=0,resetn=0,pixel_resetn=0;
    always #5 ui_clk=~ui_clk;
    always #3.7037 pixel_clk=~pixel_clk;
    wire [72:0] dp_data;wire dp_ready;wire [2239:0] ui_status;
    wire [1:0] memory_bid=0,memory_rid=0;
    wire physical_video=1;
    reg rx_clk=0;always #6.734 rx_clk=~rx_clk;
    wire rx_resetn=resetn,rx_locked=resetn;wire [383:0] input_status;
    reg [11:0] rx_x=0,rx_y=0;reg [31:0] rx_frame=0;
    always @(posedge rx_clk)begin
        if(!resetn)begin rx_x<=0;rx_y<=0;rx_frame<=0;end
        else if(rx_x==2199)begin rx_x<=0;if(rx_y==1124)begin rx_y<=0;rx_frame<=rx_frame+1;end else rx_y<=rx_y+1;end
        else rx_x<=rx_x+1;
    end
    wire rx_hs=rx_x<44,rx_vs=rx_y<5;
    wire rx_de=rx_x>=192&&rx_x<2112&&rx_y>=41&&rx_y<1121;
    wire [11:0] source_x=rx_x-192,source_y=rx_y-41;
    wire [31:0] stimulus_pixel=golden(source_y*1920+source_x,rx_frame);
    wire [23:0] rx_rbg={stimulus_pixel[7:0],stimulus_pixel[23:16],stimulus_pixel[15:8]};
    function automatic [7:0] marker_byte(input integer x,input integer frame_id);
        reg [127:0] header;
        reg [31:0] crc;
        begin
            header={32'h53524c54,8'd1,8'd2,8'd4,8'd1,32'd7,32'(frame_id/2)};
            crc=32'hffffffff;
            for(integer n=0;n<16;n=n+1)begin
                crc=crc^header[(15-n)*8+:8];
                for(integer b=0;b<8;b=b+1)crc=(crc>>1)^(crc[0]?32'hedb88320:32'b0);
            end
            crc=~crc;
            marker_byte=x<16 ? header[(15-x)*8+:8] : crc[(19-x)*8+:8];
        end
    endfunction
    function automatic [31:0] golden(input integer pixel,input integer frame_id);
        integer xx,yy;reg [7:0] marker;begin xx=pixel%1920;yy=pixel/1920;
            golden={8'b0,8'((xx>>8)^(yy>>8)^frame_id),8'(yy),8'(xx+frame_id)};
            if(xx<20 && yy<2)begin marker=marker_byte(xx,frame_id)^(yy==1?8'hff:8'h00);golden={8'b0,marker,marker,marker};end
        end
    endfunction
    // Track the external stimulus, not the bridge's accepted-frame counter.
    // Acquisition deliberately discards complete source frames. A grant must
    // refer to the most recent stimulus SOF within a bounded FIFO delay.
    integer stimulus_sof_frame=-1,accepted_source_frame=-1;
    time stimulus_sof_time=0;
    always @(posedge rx_clk)
        if(resetn&&rx_x==192&&rx_y==41)begin
            stimulus_sof_frame=rx_frame;stimulus_sof_time=$time;
        end
    always @(posedge ui_clk)
        if(resetn&&core.source_offer&&core.source_accept)begin
            if(stimulus_sof_frame<0||$time-stimulus_sof_time>100000||
                core.token[31:0]!==golden(0,stimulus_sof_frame))
                $fatal(1,"Accepted SOF does not match bounded external stimulus identity");
            accepted_source_frame=stimulus_sof_frame;
        end
    wire [31:0] m_axi_mm2s_araddr;
    wire [7:0] m_axi_mm2s_arlen;
    wire [2:0] m_axi_mm2s_arsize;
    wire [1:0] m_axi_mm2s_arburst;
    wire [2:0] m_axi_mm2s_arprot;
    wire [3:0] m_axi_mm2s_arcache;
    wire m_axi_mm2s_arvalid;
    reg m_axi_mm2s_arready=0;
    reg [127:0] m_axi_mm2s_rdata=0;
    reg [1:0] m_axi_mm2s_rresp=0;
    reg m_axi_mm2s_rlast=0;
    reg m_axi_mm2s_rvalid=0;
    wire m_axi_mm2s_rready;
    wire [31:0] m_axi_s2mm_awaddr;
    wire [7:0] m_axi_s2mm_awlen;
    wire [2:0] m_axi_s2mm_awsize;
    wire [1:0] m_axi_s2mm_awburst;
    wire [2:0] m_axi_s2mm_awprot;
    wire [3:0] m_axi_s2mm_awcache;
    wire m_axi_s2mm_awvalid;
    reg m_axi_s2mm_awready=0;
    wire [127:0] m_axi_s2mm_wdata;
    wire [15:0] m_axi_s2mm_wstrb;
    wire m_axi_s2mm_wlast;
    wire m_axi_s2mm_wvalid;
    reg m_axi_s2mm_wready=0;
    reg [1:0] m_axi_s2mm_bresp=0;
    reg m_axi_s2mm_bvalid=0;
    wire m_axi_s2mm_bready;
    bridge_core #(.BUSINESS_IDENTITY(1)) core(.*);
    reg [127:0] memory [0:1555199];
    reg wa=0,ra=0;integer wb=0,rb=0,wn=0,rn=0,windex=0,rindex=0;
    integer writes=0,reads=0,cycles=0;reg [31:0] random_state=32'h637acd19;
    always @(posedge ui_clk) begin
        cycles<=cycles+1;
        random_state<=random_state^(random_state<<13)^((random_state^(random_state<<13))>>17);
        if(cycles>40000000) $fatal(1,"VDMA test timeout");
        m_axi_s2mm_awready<=resetn && !wa && !m_axi_s2mm_bvalid;
        m_axi_s2mm_wready<=wa && random_state[1];
        if(m_axi_s2mm_awvalid && m_axi_s2mm_awready) begin
            if(m_axi_s2mm_awaddr[3:0]!=0 || m_axi_s2mm_awsize!=4 || m_axi_s2mm_awburst!=1)
                $fatal(1,"Invalid write burst");
            wa<=1;wb<=0;wn<=m_axi_s2mm_awlen+1;windex<=m_axi_s2mm_awaddr>>4;m_axi_s2mm_awready<=0;
        end
        if(m_axi_s2mm_wvalid && m_axi_s2mm_wready) begin
            if(m_axi_s2mm_wlast!=(wb==wn-1) || m_axi_s2mm_wstrb!=16'hffff || windex+wb>=1555200)
                $fatal(1,"Invalid write data");
            for(integer lane=0;lane<4;lane=lane+1)
                if(accepted_source_frame<0||m_axi_s2mm_wdata[lane*32+:32]!==golden((windex+wb-core.wr_base/16)*4+lane,accepted_source_frame))
                    $fatal(1,"HDMI input pixels/byte order differ from independent raster reference");
            memory[windex+wb]<=m_axi_s2mm_wdata;wb<=wb+1;writes<=writes+1;
            if(wb==wn-1) begin wa<=0;m_axi_s2mm_wready<=0;m_axi_s2mm_bvalid<=1;end
        end
        if(m_axi_s2mm_bvalid && m_axi_s2mm_bready)m_axi_s2mm_bvalid<=0;
        m_axi_mm2s_arready<=resetn && !ra && !m_axi_mm2s_rvalid;
        if(m_axi_mm2s_arvalid && m_axi_mm2s_arready) begin
            if(m_axi_mm2s_araddr[3:0]!=0 || m_axi_mm2s_arsize!=4 || m_axi_mm2s_arburst!=1)
                $fatal(1,"Invalid read burst");
            ra<=1;rb<=0;rn<=m_axi_mm2s_arlen+1;rindex<=m_axi_mm2s_araddr>>4;m_axi_mm2s_arready<=0;
        end
        if(m_axi_mm2s_rvalid && m_axi_mm2s_rready) begin
            m_axi_mm2s_rvalid<=0;rb<=rb+1;reads<=reads+1;if(rb==rn-1)ra<=0;
        end
        // Registered responder can replace an accepted beat without inserting
        // a mandatory idle cycle. Random bubbles remain; stalled data is held.
        if(ra && (!m_axi_mm2s_rvalid||m_axi_mm2s_rready) && random_state[3:2]!=0 &&
            !(m_axi_mm2s_rvalid&&m_axi_mm2s_rready&&rb==rn-1)) begin
            m_axi_mm2s_rvalid<=1;
            m_axi_mm2s_rdata<=memory[rindex+rb+(m_axi_mm2s_rvalid&&m_axi_mm2s_rready)];
            m_axi_mm2s_rlast<=(rb+(m_axi_mm2s_rvalid&&m_axi_mm2s_rready))==rn-1;
        end
    end

    integer buffer_source[0:2];
    wire [1663:0] sr=ui_status[2239:576];
    always @(posedge ui_clk)begin
        if(core.wr_done)buffer_source[core.scheduler.writer_index]=accepted_source_frame;
        if(resetn && (sr[34] || sr[33] || sr[4*32+:32]!=0 || sr[9*32+:32]!=0 || sr[10*32+:32]!=0))
            $fatal(1,"Business marker/accounting error %h",sr[639:0]);
        if(core.scheduler.business_accounting.tracker.scan_complete &&
            core.scheduler.business_accounting.tracker.reader_identity!==
                {1'b1,8'd2,8'd4,32'd7,32'(buffer_source[core.scheduler.reader_index]/2)})
            $fatal(1,"Decoded marker detached from independent input / DDR / scanned pixels");
        if(resetn&&core.fault)$fatal(1,"Core fault flags=%h status=%h owner=%h fifo_bad=%b golden_bad=%b frame_failed=%b fenceW=%b fenceR=%b expected=%h observed=%h rx=%d consumed=%d reads=%d",
            core.flags,core.last_status,core.owner_states,core.fifo_bad_raw,core.check_bad,core.frame_failed,
            core.wr_fence_fault,core.rd_fence_fault,core.expected_crc,core.observed_crc,core.pixels_received,core.pairs_consumed,reads);
        if(core.wr_done)$display("PROGRESS: complete written frame %d at %t",core.written_frames+1,$time);
        if(core.scheduler.scan_ui)$display("PROGRESS: complete displayed frame %d identity=%d at %t",core.displayed_frames+1,core.scheduler.reader_frame,$time);
        if(core.scheduler.writer_active&&core.scheduler.reader_active&&core.scheduler.writer_index==core.scheduler.reader_index)
            $fatal(1,"Physical read/write buffer collision");
    end
    initial begin
        repeat(50)@(negedge ui_clk);resetn=1;
        repeat(30)@(negedge pixel_clk);pixel_resetn=1;
        wait(core.displayed_frames>=5 && sr[7*32+:32]>=2);
        repeat(10)@(negedge ui_clk);
        if(core.new_frames<2||core.repeated_frames<1||core.written_frames<2||core.fault||core.damaged||core.overflow_events)
            $fatal(1,"Frame core accounting failed");
        if(sr[6*32+:32]!=core.displayed_frames || sr[7*32+:32]+sr[8*32+:32]!=sr[6*32+:32] ||
            sr[7*32+:32]>=core.new_frames || sr[8*32+:32]<=core.repeated_frames)
            $fatal(1,"Raster updates were incorrectly counted as distinct SR results");
        $display("BUSINESS new=%d repeat=%d scans=%d",sr[7*32+:32],sr[8*32+:32],sr[6*32+:32]);
        $display("PASS: sr_identity_core actual VDMA, asynchronous 1080p30 raster, golden input pixels, DDR CRC, DP60 leases and repeated scans new=%d repeated=%d",
            core.new_frames,core.repeated_frames);$finish;
    end
    initial forever begin
        #20000000;
        $display("PROGRESS simulated_ns=%t raster=%d SR_new=%d scans=%d",$time,rx_frame,sr[7*32+:32],core.displayed_frames);
        $fflush();
    end
endmodule
