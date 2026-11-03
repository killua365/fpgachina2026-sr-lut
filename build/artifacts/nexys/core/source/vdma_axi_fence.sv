// Observe both independent VDMA memory channels in the MIG UI clock domain.
// Complete means exact contiguous frame coverage and all corresponding responses.
// W may precede AW: compare independently queued cumulative burst endpoints.
module vdma_axi_fence #(parameter integer WORDS=518400)(
    input wire clk,resetn,wr_start,rd_start,input wire [31:0] wr_base,rd_base,
    output wire wr_complete,rd_complete,output reg wr_fault=0,rd_fault=0,
    output reg [31:0] words_written=0,words_read=0,
    input wire [31:0] m_axi_s2mm_awaddr,input wire [7:0] m_axi_s2mm_awlen,
    input wire [2:0] m_axi_s2mm_awsize,input wire [1:0] m_axi_s2mm_awburst,
    input wire m_axi_s2mm_awvalid,m_axi_s2mm_awready,m_axi_s2mm_wvalid,m_axi_s2mm_wready,m_axi_s2mm_wlast,
    input wire [15:0] m_axi_s2mm_wstrb,input wire m_axi_s2mm_bvalid,m_axi_s2mm_bready,input wire [1:0] m_axi_s2mm_bresp,
    input wire [31:0] m_axi_mm2s_araddr,input wire [7:0] m_axi_mm2s_arlen,
    input wire [2:0] m_axi_mm2s_arsize,input wire [1:0] m_axi_mm2s_arburst,
    input wire m_axi_mm2s_arvalid,m_axi_mm2s_arready,m_axi_mm2s_rvalid,m_axi_mm2s_rready,m_axi_mm2s_rlast,
    input wire [1:0] m_axi_mm2s_rresp
);
    reg writing=0,reading=0;reg [31:0] wbase=0,rbase=0,aw_words=0,ar_words=0;
    reg [31:0] aw_count=0,wl_count=0,b_count=0,compare_count=0,ar_count=0,rl_count=0;
    reg [31:0] aw_end[0:15],wl_end[0:15],ar_end[0:15];
    wire aw=m_axi_s2mm_awvalid&&m_axi_s2mm_awready,w=m_axi_s2mm_wvalid&&m_axi_s2mm_wready;
    wire b=m_axi_s2mm_bvalid&&m_axi_s2mm_bready,ar=m_axi_mm2s_arvalid&&m_axi_mm2s_arready;
    wire r=m_axi_mm2s_rvalid&&m_axi_mm2s_rready;
    wire [31:0] wn={24'b0,m_axi_s2mm_awlen}+1,rn={24'b0,m_axi_mm2s_arlen}+1;
    wire [31:0] read_end=ar_count==rl_count?(ar_words+rn):ar_end[rl_count[3:0]];
    assign wr_complete=writing&&!wr_fault&&words_written==WORDS&&aw_words==WORDS&&
                       aw_count==b_count&&aw_count==wl_count&&aw_count==compare_count;
    assign rd_complete=reading&&!rd_fault&&words_read==WORDS&&ar_words==WORDS&&ar_count==rl_count;
    always @(posedge clk)begin
        if(!resetn)begin
            writing<=0;reading<=0;wr_fault<=0;rd_fault<=0;words_written<=0;words_read<=0;
            aw_words<=0;ar_words<=0;aw_count<=0;wl_count<=0;b_count<=0;compare_count<=0;ar_count<=0;rl_count<=0;
        end else begin
            if(wr_start)begin
                if((writing&&!wr_complete)||aw||w||b)wr_fault<=1;
                else begin writing<=1;wbase<=wr_base;wr_fault<=0;words_written<=0;aw_words<=0;aw_count<=0;wl_count<=0;b_count<=0;compare_count<=0;end
            end else begin
                if(aw)begin
                    if(!writing || m_axi_s2mm_awsize!=4 || m_axi_s2mm_awburst!=1 || m_axi_s2mm_awaddr[3:0]!=0 ||
                       m_axi_s2mm_awaddr!=wbase+(aw_words<<4) || aw_words+wn>WORDS ||
                       {20'b0,m_axi_s2mm_awaddr[11:0]}+(wn<<4)>4096 || aw_count-compare_count>=16)wr_fault<=1;
                    aw_end[aw_count[3:0]]<=aw_words+wn;aw_words<=aw_words+wn;aw_count<=aw_count+1'b1;
                end
                if(w)begin
                    if(!writing || words_written>=WORDS || m_axi_s2mm_wstrb!=16'hffff)wr_fault<=1;
                    words_written<=words_written+1'b1;
                    if(m_axi_s2mm_wlast)begin
                        if(wl_count-compare_count>=16)wr_fault<=1;
                        wl_end[wl_count[3:0]]<=words_written+1'b1;wl_count<=wl_count+1'b1;
                    end
                end
                if(b)begin
                    if(!writing || m_axi_s2mm_bresp!=0 || b_count>=aw_count+aw || b_count>=wl_count+(w&&m_axi_s2mm_wlast))wr_fault<=1;
                    b_count<=b_count+1'b1;
                end
                if(compare_count<aw_count && compare_count<wl_count)begin
                    if(aw_end[compare_count[3:0]]!=wl_end[compare_count[3:0]])wr_fault<=1;
                    compare_count<=compare_count+1'b1;
                end
            end
            if(rd_start)begin
                if((reading&&!rd_complete)||ar||r)rd_fault<=1;
                else begin reading<=1;rbase<=rd_base;rd_fault<=0;words_read<=0;ar_words<=0;ar_count<=0;rl_count<=0;end
            end else begin
                if(ar)begin
                    if(!reading || m_axi_mm2s_arsize!=4 || m_axi_mm2s_arburst!=1 || m_axi_mm2s_araddr[3:0]!=0 ||
                       m_axi_mm2s_araddr!=rbase+(ar_words<<4) || ar_words+rn>WORDS ||
                       {20'b0,m_axi_mm2s_araddr[11:0]}+(rn<<4)>4096 || ar_count-rl_count>=16)rd_fault<=1;
                    ar_end[ar_count[3:0]]<=ar_words+rn;ar_words<=ar_words+rn;ar_count<=ar_count+1'b1;
                end
                if(r)begin
                    if(!reading || words_read>=WORDS || m_axi_mm2s_rresp!=0 ||
                       (ar_count==rl_count&&!ar) || m_axi_mm2s_rlast!=(words_read+1==read_end))rd_fault<=1;
                    words_read<=words_read+1'b1;if(m_axi_mm2s_rlast)rl_count<=rl_count+1'b1;
                end
            end
        end
    end
endmodule
