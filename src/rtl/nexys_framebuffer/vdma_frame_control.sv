// Single-shot parked descriptors. No interrupt or frame pointer is a completion fence.
// Upstream owns leases before START. COMPLETE means geometry + all AXI responses + HALTED.
// Abort/timeout is fail-stop: the integrator must drain/reset VDMA and MIG together before reuse.
module vdma_frame_control #(
    parameter integer STRIDE=7680,LINES=1080,SETTLE_CYCLES=256,TIMEOUT_CYCLES=10000000
)(
    input wire clk,resetn,
    input wire wr_start,rd_start,input wire [31:0] wr_base,rd_base,
    output wire wr_ready,rd_ready,output wire wr_armed,rd_armed,
    input wire wr_complete,rd_complete,wr_drained,rd_drained,wr_bad,rd_bad,
    output reg wr_done=0,rd_done=0,output reg fault=0,output reg [31:0] last_status=0,
    output wire [8:0] awaddr,araddr,output wire [31:0] wdata,
    output wire awvalid,wvalid,bready,arvalid,rready,
    input wire awready,wready,bvalid,arready,rvalid,input wire [1:0] bresp,rresp,input wire [31:0] rdata
);
    localparam IDLE=0,CONFIGURE=1,SETTLE=2,ARMED=3,STOP=4,POLL=5;
    reg [2:0] ws=0,rs=0;reg [2:0] wi=0,ri=0;
    reg [31:0] wbase=0,rbase=0,wtimer=0,rtimer=0;
    reg owner=0,pending=0,turn=0;
    reg command=0,command_write=0;reg [8:0] command_address=0;reg [31:0] command_data=0;
    wire command_busy,command_done,command_error,command_reset;wire [31:0] command_read;
    assign wr_ready=ws==IDLE && !fault;
    assign rd_ready=rs==IDLE && !fault;
    assign wr_armed=ws==ARMED && !fault;
    assign rd_armed=rs==ARMED && !fault;
    axi_lite_command access(.clk(clk),.resetn(resetn),.request(command),.write_command(command_write),
        .address(command_address),.write_data(command_data),.busy(command_busy),.done(command_done),
        .error(command_error),.needs_reset(command_reset),.read_data(command_read),.*);
    wire wneeds=ws==CONFIGURE || ws==STOP || ws==POLL;
    wire rneeds=rs==CONFIGURE || rs==STOP || rs==POLL;
    reg selected;reg [2:0] selected_state,selected_index;reg [8:0] cr,vs;
    always @* begin
        selected=wneeds && (!rneeds || !turn);
        selected_state=selected?ws:rs;selected_index=selected?wi:ri;
        cr=selected?9'h30:0;vs=selected?9'ha0:9'h50;
    end
    always @(posedge clk) begin
        wr_done<=0;rd_done<=0;command<=0;
        if(!resetn)begin ws<=IDLE;rs<=IDLE;wi<=0;ri<=0;wtimer<=0;rtimer<=0;pending<=0;fault<=0;turn<=0;last_status<=0;end
        else if(!fault)begin
            if(command_reset || wr_bad || rd_bad)fault<=1;
            if(wr_start && wr_ready)begin ws<=CONFIGURE;wi<=0;wbase<=wr_base;wtimer<=0;end
            if(rd_start && rd_ready)begin rs<=CONFIGURE;ri<=0;rbase<=rd_base;rtimer<=0;end
            if(ws!=IDLE)begin
                if(wtimer==TIMEOUT_CYCLES-1)fault<=1;else wtimer<=wtimer+1'b1;
                if(ws==SETTLE && wtimer==SETTLE_CYCLES-1)begin ws<=ARMED;wtimer<=0;end
                if(ws==ARMED && wr_complete && wr_drained)begin ws<=STOP;wtimer<=0;end
            end
            if(rs!=IDLE)begin
                if(rtimer==TIMEOUT_CYCLES-1)fault<=1;else rtimer<=rtimer+1'b1;
                if(rs==SETTLE && rtimer==SETTLE_CYCLES-1)begin rs<=ARMED;rtimer<=0;end
                if(rs==ARMED && rd_complete && rd_drained)begin rs<=STOP;rtimer<=0;end
            end
            if(!pending && !command_busy && !command && (wneeds||rneeds))begin
                owner<=selected;pending<=1;turn<=selected;command<=1;
                command_write<=selected_state!=POLL;
                if(selected_state==POLL)begin command_address<=cr+4;command_data<=0;end
                else if(selected_state==STOP)begin command_address<=cr;command_data<=32'h00010000;end
                else case(selected_index)
                    0:begin command_address<=cr;command_data<=32'h00010001;end
                    1:begin command_address<=vs+12;command_data<=selected?wbase:rbase;end
                    2:begin command_address<=vs+8;command_data<=STRIDE;end
                    3:begin command_address<=vs+4;command_data<=STRIDE;end
                    4:begin command_address<=vs;command_data<=LINES;end
                    default:fault<=1;
                endcase
            end
            if(command_done && pending)begin
                pending<=0;
                if(command_error)fault<=1;
                else if(owner)begin
                    case(ws)
                        CONFIGURE:if(wi==4)begin ws<=SETTLE;wtimer<=0;end else wi<=wi+1'b1;
                        STOP:ws<=POLL;
                        POLL:begin last_status<=command_read;
                            if((command_read&32'h0000cff0)!=0)fault<=1;
                            else if(command_read[0])begin ws<=IDLE;wr_done<=1;end
                        end
                    endcase
                end else begin
                    case(rs)
                        CONFIGURE:if(ri==4)begin rs<=SETTLE;rtimer<=0;end else ri<=ri+1'b1;
                        STOP:rs<=POLL;
                        POLL:begin last_status<=command_read;
                            if((command_read&32'h0000cff0)!=0)fault<=1;
                            else if(command_read[0])begin rs<=IDLE;rd_done<=1;end
                        end
                    endcase
                end
            end
        end
    end
endmodule
