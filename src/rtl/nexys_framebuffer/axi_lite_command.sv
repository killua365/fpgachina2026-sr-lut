// One command at a time. AW and W retire independently. Timeout never withdraws VALID.
module axi_lite_command #(parameter integer TIMEOUT_CYCLES=1000000)(
    input wire clk,resetn,request,write_command,input wire [8:0] address,input wire [31:0] write_data,
    output wire busy,output reg done=0,error=0,needs_reset=0,output reg [31:0] read_data=0,
    output reg [8:0] awaddr=0,araddr=0,output reg [31:0] wdata=0,
    output wire awvalid,wvalid,bready,arvalid,rready,
    input wire awready,wready,bvalid,arready,rvalid,input wire [1:0] bresp,rresp,input wire [31:0] rdata
);
    reg [1:0] state=0;reg aw_sent=0,w_sent=0,ar_sent=0;
    localparam integer CW=$clog2(TIMEOUT_CYCLES+1);
    reg [CW-1:0] watchdog=0;
    assign busy=state!=0;
    assign awvalid=resetn && state==1 && !aw_sent;
    assign wvalid=resetn && state==1 && !w_sent;
    assign bready=resetn && state==1 && aw_sent && w_sent;
    assign arvalid=resetn && state==2 && !ar_sent;
    assign rready=resetn && state==2 && ar_sent;
    wire progress=(awvalid&&awready)||(wvalid&&wready)||(arvalid&&arready)||(bready&&bvalid)||(rready&&rvalid);
    always @(posedge clk) begin
        done<=0;
        if(!resetn)begin state<=0;error<=0;needs_reset<=0;watchdog<=0;aw_sent<=0;w_sent<=0;ar_sent<=0;read_data<=0;end
        else begin
            if(busy && !needs_reset)begin
                if(progress)watchdog<=0;
                else if(watchdog==TIMEOUT_CYCLES-1)begin needs_reset<=1;error<=1;end
                else watchdog<=watchdog+1'b1;
            end
            case(state)
                0:if(request && !needs_reset)begin
                    awaddr<=address;araddr<=address;wdata<=write_data;
                    aw_sent<=0;w_sent<=0;ar_sent<=0;watchdog<=0;error<=0;state<=write_command?1:2;
                end
                1:begin
                    if(awvalid&&awready)aw_sent<=1;
                    if(wvalid&&wready)w_sent<=1;
                    if(bvalid&&bready)begin state<=0;done<=1;if(bresp!=0)error<=1;end
                end
                2:begin
                    if(arvalid&&arready)ar_sent<=1;
                    if(rvalid&&rready)begin state<=0;done<=1;read_data<=rdata;if(rresp!=0)error<=1;end
                end
            endcase
        end
    end
endmodule
